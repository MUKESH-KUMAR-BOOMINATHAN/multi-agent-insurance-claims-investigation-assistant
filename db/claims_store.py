"""
claims_store.py

SQLite persistence for submitted claims. Separate from feedback.db's
feedback table — this tracks EVERY claim that comes through the API
(pending or reviewed), which is what the dashboard list view needs.

Every operation uses a connection that is committed on success, rolled back
on error and ALWAYS closed (the previous version leaked connections when a
statement raised). The schema is created once per DB path and migrated
forward (``warnings_json`` column) without losing existing rows.

Place this file in: db/claims_store.py
"""

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, UTC
from pathlib import Path
from collections.abc import Iterator

import config

log = logging.getLogger(__name__)

DB_PATH: Path = config.CLAIMS_DB_PATH
_initialized_for: Path | None = None

# Complete, literal statements (no string building): every value is a bound parameter.
_LIST_SQL = """
    SELECT id, claim_json, fraud_result_json, similar_claims_json, recommendation_json,
           status, created_at, reviewed_at, warnings_json, similar_summary
    FROM claims ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?
"""
_GET_SQL = """
    SELECT id, claim_json, fraud_result_json, similar_claims_json, recommendation_json,
           status, created_at, reviewed_at, warnings_json, similar_summary
    FROM claims WHERE id = ?
"""


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    """Open a connection; commit on success, roll back on error, always close."""
    init_db()
    conn = sqlite3.connect(DB_PATH, timeout=10)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Create the table and apply additive migrations (idempotent, once per path)."""
    global _initialized_for
    if _initialized_for == DB_PATH:
        return
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    try:
        conn.execute("PRAGMA journal_mode=WAL")  # readers never block the writer
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
        existing = {row[1] for row in conn.execute("PRAGMA table_info(claims)")}
        if "warnings_json" not in existing:
            conn.execute("ALTER TABLE claims ADD COLUMN warnings_json TEXT")
        if "similar_summary" not in existing:
            conn.execute("ALTER TABLE claims ADD COLUMN similar_summary TEXT")
        conn.commit()
    finally:
        conn.close()
    _initialized_for = DB_PATH


def insert_claim(
    claim: dict,
    fraud_result: dict,
    similar_claims: list,
    recommendation: dict,
    warnings: list | None = None,
    similar_summary: str | None = None,
) -> int:
    """Persist a processed claim as 'pending' and return its new id."""
    with _connect() as conn:
        cursor = conn.execute(
            """INSERT INTO claims
               (claim_json, fraud_result_json, similar_claims_json, recommendation_json,
                status, created_at, warnings_json, similar_summary)
               VALUES (?, ?, ?, ?, 'pending', ?, ?, ?)""",
            (
                json.dumps(claim),
                json.dumps(fraud_result),
                json.dumps(similar_claims),
                json.dumps(recommendation),
                datetime.now(UTC).isoformat(),
                json.dumps(warnings or []),
                similar_summary,
            ),
        )
        return int(cursor.lastrowid)


def _loads(raw: str | None, default):
    """json.loads that tolerates NULL and corrupt cells instead of 500-ing a whole list."""
    if not raw:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        log.warning("Corrupt JSON cell encountered; substituting default")
        return default


def _row_to_dict(row: tuple) -> dict:
    (id_, claim_json, fraud_result_json, similar_claims_json,
     recommendation_json, status, created_at, reviewed_at, warnings_json, similar_summary) = row
    return {
        "id": id_,
        "claim": _loads(claim_json, {}),
        "fraud_result": _loads(fraud_result_json, None),
        "similar_claims": _loads(similar_claims_json, None),
        "recommendation": _loads(recommendation_json, None),
        "status": status,
        "created_at": created_at,
        "reviewed_at": reviewed_at,
        "warnings": _loads(warnings_json, []),
        "similar_claims_summary": similar_summary,
    }


def list_claims(limit: int = 100, offset: int = 0) -> list[dict]:
    """Newest-first page of claims (bounded: the list view never loads the whole table)."""
    limit = max(1, min(int(limit), 500))
    offset = max(0, int(offset))
    with _connect() as conn:
        rows = conn.execute(_LIST_SQL, (limit, offset)).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_claim(claim_id: int) -> dict | None:
    """Return one claim or None."""
    with _connect() as conn:
        row = conn.execute(_GET_SQL, (claim_id,)).fetchone()
    return _row_to_dict(row) if row else None


def mark_reviewed(claim_id: int) -> bool:
    """Flip a pending claim to reviewed. Returns False if it was not pending."""
    with _connect() as conn:
        cursor = conn.execute(
            "UPDATE claims SET status = 'reviewed', reviewed_at = ? WHERE id = ? AND status = 'pending'",
            (datetime.now(UTC).isoformat(), claim_id),
        )
        return cursor.rowcount > 0
