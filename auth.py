"""
auth.py

Authentication for the API: a single operator account, scrypt-hashed password,
short-lived signed bearer tokens (JWT, HS256).

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
from datetime import UTC, datetime, timedelta

import jwt
from fastapi import HTTPException, Request

import config

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


def authenticate(username: str, password: str) -> bool:
    """True only for the configured operator with the right password."""
    stored = config.ADMIN_PASSWORD_HASH or _DUMMY_HASH
    password_ok = verify_password(password, stored)
    user_ok = hmac.compare_digest(username.encode("utf-8"), config.ADMIN_USERNAME.encode("utf-8"))
    return bool(config.ADMIN_PASSWORD_HASH) and bool(config.ADMIN_USERNAME) and user_ok and password_ok


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------
def create_token(subject: str) -> str:
    """Issue a signed token for ``subject`` valid for TOKEN_TTL_MINUTES."""
    now = datetime.now(UTC)
    payload = {
        "sub": subject,
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


def require_user(request: Request) -> str:
    """Dependency: return the authenticated subject or raise 401."""
    if not auth_required():
        return "anonymous-dev"
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    challenge = {"WWW-Authenticate": "Bearer"}
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(status_code=401, detail="Not authenticated", headers=challenge)
    try:
        return decode_token(token.strip())
    except jwt.PyJWTError:
        # Same message for expired / tampered / wrong issuer: don't help an attacker.
        raise HTTPException(status_code=401, detail="Invalid or expired token", headers=challenge) from None


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
