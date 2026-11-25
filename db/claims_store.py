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

from sqlalchemy import Connection, delete, func, insert, or_, select, update

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
    claims.c.demo_session,
)
_NEWEST_FIRST = (claims.c.created_at.desc(), claims.c.id.desc())

# demo_session values that are not a visitor's private sandbox (see db/schema.py).
SEED = "seed"          # shared sample data that fills the dashboards
TEMPLATE = "template"  # hidden: copied into each new visitor's sandbox as starter claims
SAMPLE_SUBMITTER = "sample.customer"
DEMO_SUBMITTERS = ("demo.customer", "demo.investigator")  # who counts toward a visitor's cap


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
    demo_session: str | None = None,
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
                demo_session=demo_session,
            )
            .returning(claims.c.id)
        )
        return int(result.scalar_one())


def insert_many(rows: list[dict]) -> int:
    """Insert prepared rows (column -> value) in ONE transaction: all or nothing.

    Used to seed the sample data, so an interrupted seed leaves nothing behind
    and the next start simply tries again.
    """
    if not rows:
        return 0
    with _connect() as conn:
        conn.execute(insert(claims), rows)
    return len(rows)


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
     submitted_by, outcome, demo_session) = row
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
        "demo_session": demo_session,
    }


def _bounds(limit: int, offset: int) -> tuple[int, int]:
    return max(1, min(int(limit), 500)), max(0, int(offset))


def _visible(session: str | None):
    """Which rows a viewer may see. Real users (``session`` None): real claims plus the
    shared sample data. A demo visitor: the sample data plus their own sandbox. Another
    visitor's sandbox and the hidden templates are never visible."""
    if session is None:
        return or_(claims.c.demo_session.is_(None), claims.c.demo_session == SEED)
    return or_(claims.c.demo_session == SEED, claims.c.demo_session == session)


def is_visible(claim: dict, session: str | None) -> bool:
    """Same rule as :func:`_visible`, for a claim that is already loaded."""
    owner = claim.get("demo_session")
    return owner in (None, SEED) if session is None else owner in (SEED, session)


def list_claims(limit: int = 100, offset: int = 0, session: str | None = None) -> list[dict]:
    """Newest-first page of claims (bounded: the list view never loads the whole table)."""
    limit, offset = _bounds(limit, offset)
    query = select(*_COLUMNS).where(_visible(session)).order_by(*_NEWEST_FIRST).limit(limit).offset(offset)
    with _connect() as conn:
        rows = conn.execute(query).all()
    return [_row_to_dict(r) for r in rows]


def list_claims_for_user(username: str, limit: int = 100, offset: int = 0, session: str | None = None) -> list[dict]:
    """Newest-first page of the claims one customer submitted. A demo customer sees only
    their own sandbox (including the starter claims); a real customer never sees sandbox rows."""
    limit, offset = _bounds(limit, offset)
    if session is not None:
        owned = claims.c.demo_session == session
    else:
        owned = (claims.c.submitted_by == username) & claims.c.demo_session.is_(None)
    query = select(*_COLUMNS).where(owned).order_by(*_NEWEST_FIRST).limit(limit).offset(offset)
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


# ---------------------------------------------------------------------------
# Demo sandbox (see demo.py)
# ---------------------------------------------------------------------------
def count_session_claims(session: str) -> int:
    """Claims a demo visitor has filed themselves (the starter claims do not count)."""
    query = select(func.count()).select_from(claims).where(
        claims.c.demo_session == session, claims.c.submitted_by.in_(DEMO_SUBMITTERS)
    )
    with _connect() as conn:
        return int(conn.execute(query).scalar_one())


def count_tagged(tag: str) -> int:
    """Rows carrying this exact demo_session value (SEED or TEMPLATE)."""
    query = select(func.count()).select_from(claims).where(claims.c.demo_session == tag)
    with _connect() as conn:
        return int(conn.execute(query).scalar_one())


def clone_templates(session: str) -> int:
    """Copy the hidden template claims into a visitor's sandbox as pending starter claims,
    so the investigator view has something to review straight away."""
    cols = [c for c in _COLUMNS if c.name != "id"]
    with _connect() as conn:
        templates = conn.execute(
            select(*cols).where(claims.c.demo_session == TEMPLATE).order_by(claims.c.id)
        ).mappings().all()
        if not templates:
            return 0
        now = now_iso()
        conn.execute(
            insert(claims),
            [
                {**dict(t), "status": "pending", "reviewed_at": None, "outcome": None, "created_at": now,
                 "submitted_by": SAMPLE_SUBMITTER, "demo_session": session}
                for t in templates
            ],
        )
        return len(templates)


def purge_demo_sandboxes(older_than_iso: str) -> int:
    """Delete visitors' sandbox claims created before the cutoff (never seed or templates)."""
    with _connect() as conn:
        return conn.execute(
            delete(claims).where(
                claims.c.demo_session.is_not(None),
                claims.c.demo_session.not_in((SEED, TEMPLATE)),
                claims.c.created_at < older_than_iso,
            )
        ).rowcount


def delete_demo_data(include_seed: bool = True) -> int:
    """Remove all demo rows (sandboxes, templates and, optionally, the sample data)."""
    cond = claims.c.demo_session.is_not(None)
    if not include_seed:
        cond = cond & (claims.c.demo_session != SEED)
    with _connect() as conn:
        return conn.execute(delete(claims).where(cond)).rowcount
