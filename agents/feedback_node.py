"""
feedback_node.py

Human-in-the-loop feedback capture. NOT a LangGraph pipeline node — this runs
AFTER a human investigator reviews recommendation_node's output and confirms
the real outcome. Two things happen when feedback comes in:

  1. Persisted to the database (data/feedback.db locally, Postgres in
     production) — this becomes the source of
     truth for periodic fraud-model retraining (see retrain_model.py).
  2. Added to the similar-claims index — so the next claim investigated can
     retrieve THIS one as evidence. The index is a *derived cache* of the
     stored rows, so a failure here is logged and reported (``indexed: false``)
     but never fails or duplicates the feedback itself.

Idempotency: ``claim_id`` is UNIQUE in the feedback table. A second submission
for the same claim raises DuplicateFeedbackError instead of writing a
duplicate training row and a duplicate index document.

Place this file in: agents/feedback_node.py
"""

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, exc, insert, select

import config
from db import engine as db_engine
from db.schema import FEEDBACK_META, feedback

log = logging.getLogger(__name__)

DB_PATH: Path = config.FEEDBACK_DB_PATH
_initialized_for: str | None = None  # kept for tests that point DB_PATH elsewhere


class DuplicateFeedbackError(Exception):
    """Feedback was already recorded for this claim."""


# ---------------------------------------------------------------------------
# Storage (SQLite locally, Postgres when DATABASE_URL is set; see db/engine.py)
# ---------------------------------------------------------------------------
def database_url() -> str:
    return db_engine.url_for(DB_PATH)


@contextmanager
def _connect() -> Iterator[Connection]:
    """Commit on success, roll back on error, always release the connection."""
    with db_engine.transaction(database_url(), FEEDBACK_META) as conn:
        yield conn


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
            new_id = conn.execute(
                insert(feedback)
                .values(
                    claim_id=claim_id,
                    claim_json=json.dumps(claim),
                    fraud_probability=fraud_result.get("fraud_probability"),
                    ai_recommendation=json.dumps(recommendation),
                    investigator_decision=investigator_decision,
                    confirmed_fraud_reported=confirmed_fraud_reported,
                    created_at=datetime.now(UTC).isoformat(),
                )
                .returning(feedback.c.id)
            ).scalar_one()
            return int(new_id)
    except exc.IntegrityError as err:
        raise DuplicateFeedbackError(f"Feedback already recorded for claim {claim_id}") from err


# ---------------------------------------------------------------------------
# Retrieval index integration (best effort — derived data)
# ---------------------------------------------------------------------------
def load_indexable_feedback() -> list[tuple[dict[str, Any], str]]:
    """Every stored feedback claim as ``(claim, confirmed label)``.

    Called by the similarity index at startup, which makes the index a derived
    cache of this database: it is rebuilt from here, never persisted separately.
    """
    query = select(feedback.c.claim_json, feedback.c.confirmed_fraud_reported).order_by(feedback.c.id)
    with _connect() as conn:
        rows = conn.execute(query).all()
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
    except Exception:  # noqa: BLE001 - index is a derived cache; feedback is already safe in the database
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
