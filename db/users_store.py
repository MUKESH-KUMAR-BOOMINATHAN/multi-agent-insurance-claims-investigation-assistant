"""
users_store.py

Accounts the organization creates: investigators (staff) and customers (people
who file claims). Stored in the claims database (SQLite locally, Postgres in
production); the table is defined in db/schema.py.

The admin account is deliberately NOT stored here. It comes from the
environment (ADMIN_USERNAME / ADMIN_PASSWORD_HASH), so a lost or corrupted
database can never lock the organization out, and its username is reserved.

Customers carry a policy ``profile``: the insurer-side facts (policy limits,
premium, insured person, insured vehicle) the organization knows and the
customer cannot be expected to type in. It is merged with the incident details
the customer submits before the fraud model runs.

Usernames are unique case-insensitively (``username_key``). Password hashes
never leave this module except through :func:`get_login_record`, which auth.py
uses to verify a sign-in.
"""

from __future__ import annotations

import json

from sqlalchemy import exc, insert, select, update

from db import claims_store
from db.schema import users

ROLES = ("investigator", "customer")

_PUBLIC = (
    users.c.id, users.c.username, users.c.full_name, users.c.role, users.c.active,
    users.c.profile_json, users.c.created_at, users.c.created_by, users.c.must_change_password,
)


class DuplicateUserError(Exception):
    """The username is already taken (case-insensitive)."""


def username_key(username: str) -> str:
    return username.strip().casefold()


def _public(row) -> dict:
    id_, username, full_name, role, active, profile_json, created_at, created_by, must_change = row
    return {
        "id": id_,
        "username": username,
        "full_name": full_name,
        "role": role,
        "active": bool(active),
        "profile": json.loads(profile_json) if profile_json else None,
        "created_at": created_at,
        "created_by": created_by,
        "must_change_password": bool(must_change),
    }


def create_user(
    username: str,
    full_name: str,
    role: str,
    password_hash: str,
    created_by: str,
    profile: dict | None = None,
    must_change_password: bool = True,
) -> dict:
    """Insert an account and return its public view. Raises DuplicateUserError.

    ``must_change_password`` defaults to True: the organization chose this
    password, so the user replaces it at their first sign-in.
    """
    if role not in ROLES:
        raise ValueError(f"role must be one of {ROLES}")
    try:
        with claims_store._connect() as conn:
            new_id = conn.execute(
                insert(users)
                .values(
                    username=username,
                    username_key=username_key(username),
                    full_name=full_name,
                    role=role,
                    password_hash=password_hash,
                    active=True,
                    profile_json=json.dumps(profile) if profile is not None else None,
                    created_at=claims_store.now_iso(),
                    created_by=created_by,
                    must_change_password=must_change_password,
                )
                .returning(users.c.id)
            ).scalar_one()
    except exc.IntegrityError:
        raise DuplicateUserError(username) from None
    user = get_user(int(new_id))
    if user is None:  # just inserted; only a concurrently deleted database could do this
        raise RuntimeError("account was not persisted")
    return user


def get_user(user_id: int) -> dict | None:
    with claims_store._connect() as conn:
        row = conn.execute(select(*_PUBLIC).where(users.c.id == user_id)).first()
    return _public(row) if row else None


def get_active_user(username: str) -> dict | None:
    """Public view of an ACTIVE account, or None (unknown or deactivated)."""
    query = select(*_PUBLIC).where(users.c.username_key == username_key(username), users.c.active.is_(True))
    with claims_store._connect() as conn:
        row = conn.execute(query).first()
    return _public(row) if row else None


def get_login_record(username: str) -> tuple[dict, str] | None:
    """(public view, password hash) for an ACTIVE account. Only auth.py calls this."""
    query = select(*_PUBLIC, users.c.password_hash).where(
        users.c.username_key == username_key(username), users.c.active.is_(True)
    )
    with claims_store._connect() as conn:
        row = conn.execute(query).first()
    if not row:
        return None
    return _public(tuple(row)[:-1]), row[-1]


def list_users(roles: tuple[str, ...] = ROLES) -> list[dict]:
    """All accounts with one of ``roles``, newest first."""
    wanted = [r for r in roles if r in ROLES]
    with claims_store._connect() as conn:
        rows = conn.execute(select(*_PUBLIC).where(users.c.role.in_(wanted)).order_by(users.c.id.desc())).all()
    return [_public(r) for r in rows]


def set_active(user_id: int, active: bool) -> bool:
    with claims_store._connect() as conn:
        return conn.execute(update(users).where(users.c.id == user_id).values(active=active)).rowcount > 0


def set_password(user_id: int, password_hash: str, must_change: bool) -> bool:
    """Replace a password. ``must_change`` is True when someone else chose it (a reset)."""
    values = {"password_hash": password_hash, "must_change_password": must_change,
              "password_changed_at": claims_store.now_iso()}
    with claims_store._connect() as conn:
        return conn.execute(update(users).where(users.c.id == user_id).values(**values)).rowcount > 0
