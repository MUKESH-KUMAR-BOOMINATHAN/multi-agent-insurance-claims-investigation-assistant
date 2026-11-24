"""
auth.py

Authentication and roles for the API: scrypt-hashed passwords and short-lived
signed bearer tokens (JWT, HS256).

Roles
  * admin         the organization's owner, configured by ADMIN_USERNAME /
                  ADMIN_PASSWORD_HASH (never stored in the database). Sees all
                  claims and can create investigator and customer accounts.
  * investigator  organization staff created by the admin. Sees all claims and
                  can create customer accounts.
  * customer      a policyholder created by the organization. Can file claims and
                  see only their own claims, with a plain status (never the fraud
                  score or the AI's reasoning).

The role is NOT trusted from the token: every request re-reads the account, so a
deactivated user is locked out immediately, not when their token expires.

Design choices
  * Fail closed. Auth is ON unless AUTH_DISABLED is set, and AUTH_DISABLED is
    refused outright when ENVIRONMENT=production. If auth is on but not fully
    configured the service refuses to START (validate_auth_config) rather than
    running open.
  * Passwords are never stored or compared in plain text: scrypt with a random
    salt, constant-time comparison. Login does the same work for an unknown user
    as for a wrong password (no username-enumeration timing signal).
  * Tokens carry only the subject, issue/expiry times and an issuer; they are
    verified for signature, expiry and issuer. The signing secret must be >= 32
    characters and different per environment.
  * The bearer token is meant to be held in memory by the browser app (not in
    localStorage), so an XSS bug cannot read it from storage.

Generate a password hash with:  python scripts/hash_password.py
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from dataclasses import dataclass
from typing import Annotated
from datetime import UTC, datetime, timedelta

import jwt
from fastapi import Depends, HTTPException, Request

import config
from db import users_store

log = logging.getLogger(__name__)

ISSUER = "claimguard"
ALGORITHM = "HS256"
MIN_SECRET_LENGTH = 32

_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32


# ---------------------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------------------
def _scrypt(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=_SCRYPT_DKLEN)


def hash_password(password: str) -> str:
    """Return ``scrypt$N$r$p$<salt hex>$<hash hex>`` for storage in ADMIN_PASSWORD_HASH."""
    salt = secrets.token_bytes(16)
    digest = _scrypt(password, salt, _SCRYPT_N, _SCRYPT_R, _SCRYPT_P)
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Constant-time check of ``password`` against a :func:`hash_password` string.

    Any malformed stored value returns False (never raises).
    """
    try:
        scheme, n, r, p, salt_hex, digest_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = bytes.fromhex(digest_hex)
        actual = _scrypt(password, bytes.fromhex(salt_hex), int(n), int(r), int(p))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


# Used so an unknown username costs the same as a wrong password.
_DUMMY_HASH = hash_password(secrets.token_hex(8))


@dataclass(frozen=True)
class User:
    username: str
    role: str  # admin | investigator | customer
    full_name: str
    # True while the account still has a password someone else chose (new
    # account, reset). Such a token may only change the password.
    must_change_password: bool = False

    @property
    def is_org(self) -> bool:
        return self.role in ("admin", "investigator")


def _is_admin_username(username: str) -> bool:
    return bool(config.ADMIN_USERNAME) and hmac.compare_digest(
        username.casefold().encode("utf-8"), config.ADMIN_USERNAME.casefold().encode("utf-8")
    )


def _from_account(account: dict) -> User:
    return User(
        username=account["username"],
        role=account["role"],
        full_name=account["full_name"],
        must_change_password=bool(account.get("must_change_password")),
    )


def _admin_user() -> User:
    return User(username=config.ADMIN_USERNAME, role="admin", full_name="Administrator")


def authenticate(username: str, password: str) -> User | None:
    """The account for these credentials, or None.

    Every path verifies exactly one scrypt hash, so an unknown username, a
    deactivated account and a wrong password all take the same time.
    """
    if _is_admin_username(username):
        stored = config.ADMIN_PASSWORD_HASH or _DUMMY_HASH
        ok = verify_password(password, stored)
        return _admin_user() if ok and config.ADMIN_PASSWORD_HASH else None

    record = users_store.get_login_record(username) if username else None
    if record is None:
        verify_password(password, _DUMMY_HASH)
        return None
    account, stored = record
    if not verify_password(password, stored):
        return None
    return _from_account(account)


def resolve_user(username: str) -> User | None:
    """Current account for a token subject (None if deleted or deactivated)."""
    if _is_admin_username(username):
        return _admin_user() if config.ADMIN_PASSWORD_HASH else None
    account = users_store.get_active_user(username)
    if account is None:
        return None
    return _from_account(account)


def is_reserved_username(username: str) -> bool:
    """The admin's username can never be taken by a database account."""
    return _is_admin_username(username)


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------
def create_token(subject: str, role: str | None = None) -> str:
    """Issue a signed token for ``subject`` valid for TOKEN_TTL_MINUTES.

    ``role`` is included for the client's convenience only; the server always
    re-reads the account (see :func:`require_user`).
    """
    now = datetime.now(UTC)
    payload = {
        "sub": subject,
        **({"role": role} if role else {}),
        "iss": ISSUER,
        "iat": now,
        "exp": now + timedelta(minutes=config.TOKEN_TTL_MINUTES),
    }
    return jwt.encode(payload, config.JWT_SECRET, algorithm=ALGORITHM)


def decode_token(token: str) -> str:
    """Return the token's subject, or raise ``jwt.PyJWTError`` if it is invalid/expired."""
    claims = jwt.decode(
        token,
        config.JWT_SECRET,
        algorithms=[ALGORITHM],  # pinned: never trust the token's own "alg" header
        issuer=ISSUER,
        options={"require": ["exp", "iat", "sub", "iss"]},
    )
    return str(claims["sub"])


# ---------------------------------------------------------------------------
# FastAPI integration
# ---------------------------------------------------------------------------
def auth_required() -> bool:
    """False only in development with AUTH_DISABLED set (never in production)."""
    return not (config.AUTH_DISABLED and not config.IS_PRODUCTION)


DEV_USER = User(username="dev", role="admin", full_name="Developer (auth disabled)")


def require_account(request: Request) -> User:
    """Dependency: the authenticated, still-active account, or 401.

    Allows an account that must still change its password; use only on the
    routes such an account needs (who am I, change password).
    """
    if not auth_required():
        return DEV_USER
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    challenge = {"WWW-Authenticate": "Bearer"}
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(status_code=401, detail="Not authenticated", headers=challenge)
    try:
        subject = decode_token(token.strip())
    except jwt.PyJWTError:
        # Same message for expired / tampered / wrong issuer: don't help an attacker.
        raise HTTPException(status_code=401, detail="Invalid or expired token", headers=challenge) from None
    user = resolve_user(subject)
    if user is None:  # account deactivated (or removed) since the token was issued
        raise HTTPException(status_code=401, detail="Invalid or expired token", headers=challenge)
    return user


def require_user(user: Annotated[User, Depends(require_account)]) -> User:
    """Dependency: an authenticated account that has set its own password."""
    if user.must_change_password:
        raise HTTPException(status_code=403, detail="You must change your temporary password first")
    return user


def require_org(user: Annotated[User, Depends(require_user)]) -> User:
    """Dependency: admin or investigator."""
    if not user.is_org:
        raise HTTPException(status_code=403, detail="Organization access only")
    return user


def require_admin(user: Annotated[User, Depends(require_user)]) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Administrator access only")
    return user


def require_customer(user: Annotated[User, Depends(require_user)]) -> User:
    if user.role != "customer":
        raise HTTPException(status_code=403, detail="Customer access only")
    return user


def validate_auth_config() -> None:
    """Called at startup. Raises RuntimeError for any unsafe configuration."""
    if config.AUTH_DISABLED:
        if config.IS_PRODUCTION:
            raise RuntimeError("AUTH_DISABLED must not be set when ENVIRONMENT=production.")
        log.warning("Authentication is DISABLED (development only). Never deploy like this.")
        return

    missing = [
        name
        for name, value in (
            ("ADMIN_USERNAME", config.ADMIN_USERNAME),
            ("ADMIN_PASSWORD_HASH", config.ADMIN_PASSWORD_HASH),
            ("JWT_SECRET", config.JWT_SECRET),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(
            f"Authentication is enabled but {', '.join(missing)} is not set. "
            "Set them (see .env.example), or AUTH_DISABLED=true for local development only."
        )
    if len(config.JWT_SECRET) < MIN_SECRET_LENGTH:
        raise RuntimeError(f"JWT_SECRET must be at least {MIN_SECRET_LENGTH} characters.")
    if not config.ADMIN_PASSWORD_HASH.startswith("scrypt$"):
        raise RuntimeError("ADMIN_PASSWORD_HASH must come from scripts/hash_password.py (scrypt format).")
