"""
feedback_node.py

Human-in-the-loop feedback capture. NOT a LangGraph pipeline node — this runs
AFTER a human investigator reviews recommendation_node's output and confirms
the real outcome. Two things happen when feedback comes in:

  1. Persisted to SQLite (data/feedback.db) — this becomes the source of
     truth for periodic fraud-model retraining (see retrain_model.py).
  2. Added immediately to the Chroma retrieval index — so the very next
     claim investigated can retrieve THIS one as evidence. Vector indexes
     support incremental inserts, unlike the Random Forest classifier,
     which can only be updated via full batch retrain.

Place this file in: agents/feedback_node.py
"""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from llama_index.core import Document
from retrieval.chroma_client import get_index

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "feedback.db"


# ---------------------------------------------------------------------------
# SQLite storage
# ---------------------------------------------------------------------------
def _init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
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
    conn.commit()
    conn.close()


def _save_feedback(
    claim: dict,
    fraud_result: dict,
    recommendation: dict,
    investigator_decision: str,
    confirmed_fraud_reported: str,
    claim_id: int | None = None,
) -> int:
    _init_db()
    conn = sqlite3.connect(DB_PATH)
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
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    conn.commit()
    row_id = cursor.lastrowid
    conn.close()
    return row_id


# ---------------------------------------------------------------------------
# Live Chroma index update
# ---------------------------------------------------------------------------

def _claim_to_text(claim: dict) -> str:
    """Same summary format as build_index.py / retrieval_node.py, so this
    new document embeds into the same space as the historical ones."""
    parts = [f"{k.replace('_', ' ')}: {v}" for k, v in claim.items() if v not in (None, "", "?")]
    return "Claim record — " + "; ".join(parts)


def _add_to_index(claim: dict, confirmed_fraud_reported: str) -> None:
    index = get_index()
    doc = Document(
        text=_claim_to_text(claim),
        metadata={
            "fraud_reported": confirmed_fraud_reported,
            "incident_type": claim.get("incident_type"),
            "incident_severity": claim.get("incident_severity"),
            "source": "investigator_feedback",
        },
    )
    index.insert(doc)  # incremental — no reindex of the whole collection needed


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
        investigator_decision: e.g. "agreed", "overridden_to_approve",
            "overridden_to_investigate" — free text for now, your call on an enum later
        confirmed_fraud_reported: "Y" or "N" — the ACTUAL confirmed outcome,
            in the same format as the training data's fraud_reported column
        claim_id: the claims_store.py row ID this feedback belongs to, if
            submitted via the API — lets the dashboard link a claim to its
            eventual outcome

    Returns:
        {"status": "ok", "feedback_id": <int>}
    """
    if confirmed_fraud_reported not in ("Y", "N"):
        raise ValueError("confirmed_fraud_reported must be 'Y' or 'N' to match training data format")

    feedback_id = _save_feedback(
        claim, fraud_result, recommendation, investigator_decision, confirmed_fraud_reported, claim_id
    )
    _add_to_index(claim, confirmed_fraud_reported)

    return {"status": "ok", "feedback_id": feedback_id}


if __name__ == "__main__":
    # smoke test
    test_claim = {
        "incident_type": "Single Vehicle Collision",
        "incident_severity": "Major Damage",
        "auto_make": "Honda",
        "auto_model": "Civic",
        "total_claim_amount": 58000,
    }
    result = feedback_node(
        claim=test_claim,
        fraud_result={"fraud_probability": 0.869, "status": "ok"},
        recommendation={"recommendation": "investigate", "confidence": 0.9},
        investigator_decision="agreed",
        confirmed_fraud_reported="Y",
    )
    print(result)
    print(f"Check {DB_PATH} for the new row, and query retrieval_node again — "
          f"this claim should now show up as a similar-claims match.")