"""
claims_store.py

SQLite persistence for submitted claims. Separate from feedback.db's
feedback table — this tracks EVERY claim that comes through the API
(pending or reviewed), which is what the dashboard list view needs.

Place this file in: db/claims_store.py
"""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "claims.db"


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS claims (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            claim_json TEXT NOT NULL,
            fraud_result_json TEXT,
            similar_claims_json TEXT,
            recommendation_json TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            reviewed_at TEXT
        )
    """)
    conn.commit()
    conn.close()


def insert_claim(
    claim: dict,
    fraud_result: dict,
    similar_claims: list,
    recommendation: dict,
) -> int:
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.execute(
        """INSERT INTO claims
           (claim_json, fraud_result_json, similar_claims_json, recommendation_json,
            status, created_at)
           VALUES (?, ?, ?, ?, 'pending', ?)""",
        (
            json.dumps(claim),
            json.dumps(fraud_result),
            json.dumps(similar_claims),
            json.dumps(recommendation),
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    conn.commit()
    claim_id = cursor.lastrowid
    conn.close()
    return claim_id


def _row_to_dict(row: tuple) -> dict:
    (id_, claim_json, fraud_result_json, similar_claims_json,
     recommendation_json, status, created_at, reviewed_at) = row
    return {
        "id": id_,
        "claim": json.loads(claim_json),
        "fraud_result": json.loads(fraud_result_json) if fraud_result_json else None,
        "similar_claims": json.loads(similar_claims_json) if similar_claims_json else None,
        "recommendation": json.loads(recommendation_json) if recommendation_json else None,
        "status": status,
        "created_at": created_at,
        "reviewed_at": reviewed_at,
    }


def list_claims() -> list[dict]:
    init_db()
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        """SELECT id, claim_json, fraud_result_json, similar_claims_json,
                  recommendation_json, status, created_at, reviewed_at
           FROM claims ORDER BY created_at DESC"""
    ).fetchall()
    conn.close()
    return [_row_to_dict(r) for r in rows]


def get_claim(claim_id: int) -> Optional[dict]:
    init_db()
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        """SELECT id, claim_json, fraud_result_json, similar_claims_json,
                  recommendation_json, status, created_at, reviewed_at
           FROM claims WHERE id = ?""",
        (claim_id,),
    ).fetchone()
    conn.close()
    return _row_to_dict(row) if row else None


def mark_reviewed(claim_id: int) -> None:
    init_db()
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "UPDATE claims SET status = 'reviewed', reviewed_at = ? WHERE id = ?",
        (datetime.now(timezone.utc).isoformat(), claim_id),
    )
    conn.commit()
    conn.close()