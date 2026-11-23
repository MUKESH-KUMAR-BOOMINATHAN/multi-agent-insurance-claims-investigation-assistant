"""
feedback_node.py

Human-in-the-loop feedback capture. NOT a LangGraph pipeline node — this runs
AFTER a human investigator reviews recommendation_node's output and confirms
the real outcome. Two things happen when feedback comes in:

  1. Persisted to SQLite (data/feedback.db) — this becomes the source of
     truth for periodic fraud-model retraining (see retrain_model.py).
  2. Added to the Chroma retrieval index — so the next claim investigated can
     retrieve THIS one as evidence. The index is a *derived cache* of the
     SQLite rows, so a failure here is logged and reported (``indexed: false``)
     but never fails or duplicates the feedback itself.

Idempotency: ``claim_id`` is UNIQUE in the feedback table. A second submission
for the same claim raises DuplicateFeedbackError instead of writing a
duplicate training row and a duplicate index document.

Place this file in: agents/feedback_node.py
"""

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, UTC
from pathlib import Path
from typing import Any
from collections.abc import Iterator

import config

log = logging.getLogger(__name__)

DB_PATH: Path = config.FEEDBACK_DB_PATH
_initialized_for: Path | None = None


class DuplicateFeedbackError(Exception):
    """Feedback was already recorded for this claim."""


# ---------------------------------------------------------------------------
# SQLite storage
# ---------------------------------------------------------------------------
@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    """Commit on success, roll back on error, always close."""
    _init_db()
    conn = sqlite3.connect(DB_PATH, timeout=10)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _init_db() -> None:
    """Create the table + unique-per-claim index once per DB path."""
    global _initialized_for
    if _initialized_for == DB_PATH:
        return
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    try:
        conn.execute("PRAGMA journal_mode=WAL")  # readers never block the writer
        conn.execute("""
            CREATE TABLE IF NOT EXISTS feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                claim_id INTEGER,
                claim_json TEXT NOT NULL,
                fraud_probability REAL,
                ai_recommendation TEXT,
                investigator_decision TEXT NOT NULL,
                confirmed_fraud_reported TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        try:
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_feedback_claim_id "
                "ON feedback(claim_id) WHERE claim_id IS NOT NULL"
            )
        except sqlite3.IntegrityError:
            # Pre-existing duplicate rows from before this guard existed:
            # keep working, but say so loudly rather than failing startup.
            log.error("feedback table already contains duplicate claim_ids; uniqueness NOT enforced")
        conn.commit()
    finally:
        conn.close()
    _initialized_for = DB_PATH


def _save_feedback(
    claim: dict,
    fraud_result: dict,
    recommendation: dict,
    investigator_decision: str,
    confirmed_fraud_reported: str,
    claim_id: int | None = None,
) -> int:
    try:
        with _connect() as conn:
            cursor = conn.execute(
                """INSERT INTO feedback
                   (claim_id, claim_json, fraud_probability, ai_recommendation, investigator_decision,
                    confirmed_fraud_reported, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    claim_id,
                    json.dumps(claim),
                    fraud_result.get("fraud_probability"),
                    json.dumps(recommendation),
                    investigator_decision,
                    confirmed_fraud_reported,
                    datetime.now(UTC).isoformat(),
                ),
            )
            return int(cursor.lastrowid)
    except sqlite3.IntegrityError as exc:
        raise DuplicateFeedbackError(f"Feedback already recorded for claim {claim_id}") from exc


# ---------------------------------------------------------------------------
# Retrieval index integration (best effort — derived data)
# ---------------------------------------------------------------------------
def load_indexable_feedback() -> list[tuple[dict[str, Any], str]]:
    """Every stored feedback claim as ``(claim, confirmed label)``.

    Called by the similarity index at startup, which makes the index a derived
    cache of this database: it is rebuilt from here, never persisted separately.
    """
    with _connect() as conn:
        rows = conn.execute("SELECT claim_json, confirmed_fraud_reported FROM feedback ORDER BY id").fetchall()
    result: list[tuple[dict[str, Any], str]] = []
    for claim_json, label in rows:
        try:
            result.append((json.loads(claim_json), label))
        except json.JSONDecodeError:
            log.warning("Skipping a feedback row with corrupt JSON")
    return result


def _add_to_index(claim: dict, confirmed_fraud_reported: str) -> bool:
    """Make the confirmed claim searchable immediately. Returns success.

    Imported lazily so feedback persistence works even if retrieval is broken.
    """
    try:
        from retrieval import similarity

        # If the index is created by this very call, its startup loader already
        # reads the row we just saved; adding it again would count it twice.
        was_ready = similarity.status()["ready"]
        index = similarity.get_index()
        if was_ready:
            index.add_feedback(claim, confirmed_fraud_reported)
        return True
    except Exception:  # noqa: BLE001 - index is a derived cache; feedback is already safe in SQLite
        log.exception("Could not add feedback to the retrieval index (feedback itself was saved)")
        return False


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def feedback_node(
    claim: dict,
    fraud_result: dict,
    recommendation: dict,
    investigator_decision: str,
    confirmed_fraud_reported: str,
    claim_id: int | None = None,
) -> dict:
    """
    Args:
        claim: the original claim dict that was investigated
        fraud_result: fraud_node's output for this claim
        recommendation: recommendation_node's output for this claim
        investigator_decision: "agreed" | "overridden_to_approve" |
            "overridden_to_investigate"
        confirmed_fraud_reported: "Y" or "N" — the ACTUAL confirmed outcome,
            in the same format as the training data's fraud_reported column
        claim_id: the claims_store.py row ID this feedback belongs to, if
            submitted via the API

    Returns:
        {"status": "ok", "feedback_id": <int>, "indexed": <bool>}

    Raises:
        ValueError: confirmed_fraud_reported is not "Y"/"N".
        DuplicateFeedbackError: feedback already exists for ``claim_id``.
    """
    if confirmed_fraud_reported not in ("Y", "N"):
        raise ValueError("confirmed_fraud_reported must be 'Y' or 'N' to match training data format")

    feedback_id = _save_feedback(
        claim, fraud_result, recommendation, investigator_decision, confirmed_fraud_reported, claim_id
    )
    indexed = _add_to_index(claim, confirmed_fraud_reported)

    return {"status": "ok", "feedback_id": feedback_id, "indexed": indexed}


if __name__ == "__main__":
    # smoke test (writes to the configured feedback DB — set FEEDBACK_DB_PATH to a scratch file)
    test_claim = {
        "incident_type": "Single Vehicle Collision",
        "incident_severity": "Major Damage",
        "auto_make": "Honda",
        "auto_model": "Civic",
        "total_claim_amount": 58000,
    }
    print(
        feedback_node(
            claim=test_claim,
            fraud_result={"fraud_probability": 0.869, "status": "ok"},
            recommendation={"recommendation": "investigate", "confidence": 0.9},
            investigator_decision="agreed",
            confirmed_fraud_reported="Y",
        )
    )
