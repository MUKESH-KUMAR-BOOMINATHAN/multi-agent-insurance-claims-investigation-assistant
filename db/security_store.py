"""
security_store.py

Two small, persistent security records, kept in the database rather than in
memory so they survive restarts and a free host going to sleep:

  * login_throttle  consecutive failed sign-ins per username, and a temporary
                    lock after too many (on top of the per-IP rate limit).
  * audit_events    who did what (sign-ins, account changes, decisions), for
                    the administrator. Never holds passwords, tokens or claim data.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, insert, select, update

import config
from db import claims_store
from db.schema import audit_events, login_throttle


def _now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Account lockout
# ---------------------------------------------------------------------------
def seconds_locked(key: str) -> float | None:
    """Seconds until ``key`` may try again, or None if it is not locked."""
    with claims_store._connect() as conn:
        locked_until = conn.execute(
            select(login_throttle.c.locked_until).where(login_throttle.c.username_key == key)
        ).scalar()
    if not locked_until:
        return None
    remaining = (datetime.fromisoformat(locked_until) - _now()).total_seconds()
    return remaining if remaining > 0 else None


def record_failure(key: str) -> bool:
    """Count a failed sign-in. Returns True if this failure locked the account."""
    with claims_store._connect() as conn:
        failures = conn.execute(
            select(login_throttle.c.failures).where(login_throttle.c.username_key == key)
        ).scalar()
        if failures is None:
            conn.execute(insert(login_throttle).values(username_key=key, failures=1))
            failures = 1
        else:
            failures += 1
            conn.execute(update(login_throttle).where(login_throttle.c.username_key == key).values(failures=failures))
        if failures >= config.LOCKOUT_AFTER_FAILURES:
            until = _now() + timedelta(minutes=config.LOCKOUT_MINUTES)
            conn.execute(
                update(login_throttle)
                .where(login_throttle.c.username_key == key)
                .values(failures=0, locked_until=until.isoformat())
            )
            return True
    return False


def clear_failures(key: str) -> None:
    with claims_store._connect() as conn:
        conn.execute(delete(login_throttle).where(login_throttle.c.username_key == key))


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------
def audit(action: str, actor: str | None = None, target: str | None = None, ip: str | None = None) -> None:
    """Record one event. Callers pass identifiers only, never secrets or claim contents."""
    with claims_store._connect() as conn:
        conn.execute(
            insert(audit_events).values(
                at=_now().isoformat(),
                actor=(actor or "")[:100] or None,
                action=action[:50],
                target=(target or "")[:200] or None,
                ip=(ip or "")[:64] or None,
            )
        )


def list_audit(limit: int = 200) -> list[dict]:
    limit = max(1, min(int(limit), 1000))
    query = select(audit_events).order_by(audit_events.c.id.desc()).limit(limit)
    with claims_store._connect() as conn:
        rows = conn.execute(query).mappings().all()
    return [dict(r) for r in rows]
