"""
claims_store.py

Persistence for submitted claims: EVERY claim that comes through the API
(pending or reviewed), which is what the dashboards need. Runs on SQLite
locally and on Postgres when DATABASE_URL is set (see db/engine.py).

Every operation runs in a transaction that commits on success, rolls back on
error and always releases its connection. The schema is created and migrated
forward automatically without losing existing rows.
"""

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import Connection, insert, select, update

import config
from db import engine as db_engine
from db.schema import CLAIMS_META, claims

log = logging.getLogger(__name__)

DB_PATH: Path = config.CLAIMS_DB_PATH
_initialized_for: str | None = None  # kept for tests that point DB_PATH elsewhere

_COLUMNS = (
    claims.c.id, claims.c.claim_json, claims.c.fraud_result_json, claims.c.similar_claims_json,
    claims.c.recommendation_json, claims.c.status, claims.c.created_at, claims.c.reviewed_at,
    claims.c.warnings_json, claims.c.similar_summary, claims.c.submitted_by, claims.c.outcome,
)
_NEWEST_FIRST = (claims.c.created_at.desc(), claims.c.id.desc())


def database_url() -> str:
    return db_engine.url_for(DB_PATH)


@contextmanager
def _connect() -> Iterator[Connection]:
    """A transaction on the claims database (also holds accounts and the audit log)."""
    with db_engine.transaction(database_url(), CLAIMS_META) as conn:
        yield conn


def init_db() -> None:
    """Create/migrate the schema now (called at startup so the first request is fast)."""
    db_engine.ensure_schema(database_url(), CLAIMS_META)


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def insert_claim(
    claim: dict,
    fraud_result: dict,
    similar_claims: list,
    recommendation: dict,
    warnings: list | None = None,
    similar_summary: str | None = None,
    submitted_by: str | None = None,
) -> int:
    """Persist a processed claim as 'pending' and return its new id."""
    with _connect() as conn:
        result = conn.execute(
            insert(claims)
            .values(
                claim_json=json.dumps(claim),
                fraud_result_json=json.dumps(fraud_result),
                similar_claims_json=json.dumps(similar_claims),
                recommendation_json=json.dumps(recommendation),
                status="pending",
                created_at=now_iso(),
                warnings_json=json.dumps(warnings or []),
                similar_summary=similar_summary,
                submitted_by=submitted_by,
            )
            .returning(claims.c.id)
        )
        return int(result.scalar_one())


def _loads(raw: str | None, default):
    """json.loads that tolerates NULL and corrupt cells instead of 500-ing a whole list."""
    if not raw:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        log.warning("Corrupt JSON cell encountered; substituting default")
        return default


def _row_to_dict(row) -> dict:
    (id_, claim_json, fraud_result_json, similar_claims_json,
     recommendation_json, status, created_at, reviewed_at, warnings_json, similar_summary,
     submitted_by, outcome) = row
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
        "submitted_by": submitted_by,
        "outcome": outcome,
    }


def _bounds(limit: int, offset: int) -> tuple[int, int]:
    return max(1, min(int(limit), 500)), max(0, int(offset))


def list_claims(limit: int = 100, offset: int = 0) -> list[dict]:
    """Newest-first page of claims (bounded: the list view never loads the whole table)."""
    limit, offset = _bounds(limit, offset)
    with _connect() as conn:
        rows = conn.execute(select(*_COLUMNS).order_by(*_NEWEST_FIRST).limit(limit).offset(offset)).all()
    return [_row_to_dict(r) for r in rows]


def list_claims_for_user(username: str, limit: int = 100, offset: int = 0) -> list[dict]:
    """Newest-first page of the claims one customer submitted."""
    limit, offset = _bounds(limit, offset)
    query = (
        select(*_COLUMNS)
        .where(claims.c.submitted_by == username)
        .order_by(*_NEWEST_FIRST)
        .limit(limit)
        .offset(offset)
    )
    with _connect() as conn:
        rows = conn.execute(query).all()
    return [_row_to_dict(r) for r in rows]


def get_claim(claim_id: int) -> dict | None:
    """Return one claim or None."""
    with _connect() as conn:
        row = conn.execute(select(*_COLUMNS).where(claims.c.id == claim_id)).first()
    return _row_to_dict(row) if row else None


def mark_reviewed(claim_id: int, outcome: str | None = None) -> bool:
    """Flip a pending claim to reviewed, recording the customer-facing outcome
    ('approved' | 'not_approved'). Returns False if it was not pending."""
    with _connect() as conn:
        result = conn.execute(
            update(claims)
            .where(claims.c.id == claim_id, claims.c.status == "pending")
            .values(status="reviewed", reviewed_at=now_iso(), outcome=outcome)
        )
        return result.rowcount > 0
