"""
config.py

Central, environment-driven configuration. Every tunable (paths, Groq API
limits, rate limits) lives here so no module hardcodes them, and so tests /
demos can redirect storage with environment variables.

Secrets: the Groq API key is read from the environment (or an untracked
``.env`` file) at call time via :func:`get_groq_api_key`. It is never logged
and never written to disk by this project. See ``.env.example``.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

_log = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    """Read an integer env var; fall back to ``default`` on a bad value."""
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        _log.warning("Ignoring non-integer %s; using default %s", name, default)
        return default


def _env_float(name: str, default: float) -> float:
    """Read a float env var; fall back to ``default`` on a bad value."""
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        _log.warning("Ignoring non-numeric %s; using default %s", name, default)
        return default


def _env_path(name: str, default_relative: str) -> Path:
    """Read a path env var; relative paths resolve against the project root."""
    path = Path(os.getenv(name) or default_relative)
    return path if path.is_absolute() else ROOT / path


# --- Environment ------------------------------------------------------------
ENVIRONMENT = os.getenv("ENVIRONMENT", "development").strip().lower()
IS_PRODUCTION = ENVIRONMENT == "production"


def _env_bool(name: str, default: bool = False) -> bool:
    """Read a boolean env var (1/true/yes/on)."""
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


# --- Storage ---------------------------------------------------------------
# DATABASE_URL: a Postgres connection string (e.g. from Neon's free tier). When
# set, ALL data (claims, accounts, feedback, audit log) lives there, which is
# what a host with an ephemeral filesystem such as Render's free plan needs.
# When unset, SQLite files under DATA_DIR are used (local development, tests);
# on a host, files there are lost on every deploy/restart unless DATA_DIR is a
# persistent disk.
DATABASE_URL = os.getenv("DATABASE_URL", "").strip() or None
DATA_DIR = _env_path("DATA_DIR", "data")
CLAIMS_DB_PATH = _env_path("CLAIMS_DB_PATH", str(DATA_DIR / "claims.db"))
FEEDBACK_DB_PATH = _env_path("FEEDBACK_DB_PATH", str(DATA_DIR / "feedback.db"))
MODEL_PATH = _env_path("MODEL_PATH", "models/model.pkl")
CSV_PATH = _env_path("CSV_PATH", "data/insurance_claims.csv")

# --- Groq (OpenAI-compatible chat completions) --------------------------------
GROQ_API_URL = os.getenv("GROQ_API_URL", "https://api.groq.com/openai/v1/chat/completions")
# A production model from https://console.groq.com/docs/models that supports JSON mode.
# Which models are usable depends on your Groq organisation: check
# `GET https://api.groq.com/openai/v1/models` with your key.
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")
# gpt-oss models are reasoning models whose hidden reasoning tokens count toward
# the output cap; "low" keeps answers well inside it (measured: ~175 vs ~495 tokens
# of a 600 cap). Set to an empty string for non-reasoning models (e.g. Llama):
# Groq answers HTTP 400 to a reasoning_effort value the model doesn't support.
GROQ_REASONING_EFFORT = os.getenv("GROQ_REASONING_EFFORT", "low").strip()
GROQ_TIMEOUT_S = _env_float("GROQ_TIMEOUT_S", 20.0)
# Hard cap on generated tokens per call (Groq field: max_completion_tokens).
GROQ_MAX_OUTPUT_TOKENS = _env_int("GROQ_MAX_OUTPUT_TOKENS", 600)
# Extra attempts after the first on a transient (429/5xx/timeout) failure.
GROQ_MAX_RETRIES = _env_int("GROQ_MAX_RETRIES", 1)
# On HTTP 429 Groq sends `retry-after` (seconds). Wait for it only if it is this
# short; a longer wait would stall the request, so fall back to rules instead.
GROQ_MAX_RETRY_WAIT_S = _env_float("GROQ_MAX_RETRY_WAIT_S", 5.0)
# Spend guards: every HTTP attempt counts against both windows.
GROQ_MAX_CALLS_PER_MINUTE = _env_int("GROQ_MAX_CALLS_PER_MINUTE", 10)
GROQ_MAX_CALLS_PER_DAY = _env_int("GROQ_MAX_CALLS_PER_DAY", 200)
# Circuit breaker: open after N consecutive failures, retry after cooldown.
GROQ_BREAKER_FAILURES = _env_int("GROQ_BREAKER_FAILURES", 3)
GROQ_BREAKER_COOLDOWN_S = _env_float("GROQ_BREAKER_COOLDOWN_S", 60.0)
# Identical (fraud result + evidence) prompts reuse the previous answer.
GROQ_CACHE_SIZE = _env_int("GROQ_CACHE_SIZE", 256)
GROQ_CACHE_TTL_S = _env_float("GROQ_CACHE_TTL_S", 3600.0)

# --- API --------------------------------------------------------------------
# Browser origins allowed to call the API (the deployed frontend URL(s)).
# Never "*": the API takes bearer tokens and returns claim data.
CORS_ORIGINS = [
    o.strip()
    for o in os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",")
    if o.strip()
]
# Per-client limit on POST /claims (each submission can trigger a Groq call).
CLAIMS_PER_MINUTE_PER_CLIENT = _env_int("CLAIMS_PER_MINUTE_PER_CLIENT", 20)
# Reject request bodies larger than this (a valid claim is well under 2 KB).
MAX_BODY_BYTES = _env_int("MAX_BODY_BYTES", 64 * 1024)

# --- Authentication -----------------------------------------------------------
# Auth is ON unless AUTH_DISABLED is set, and AUTH_DISABLED is refused when
# ENVIRONMENT=production (see auth.validate_auth_config): fail closed.
AUTH_DISABLED = _env_bool("AUTH_DISABLED", False)
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "").strip()
# Produce with: python scripts/hash_password.py   (scrypt, never a plain password)
ADMIN_PASSWORD_HASH = os.getenv("ADMIN_PASSWORD_HASH", "").strip()
JWT_SECRET = os.getenv("JWT_SECRET", "").strip()  # >= 32 chars; random per environment
TOKEN_TTL_MINUTES = _env_int("TOKEN_TTL_MINUTES", 480)
LOGIN_ATTEMPTS_PER_MINUTE = _env_int("LOGIN_ATTEMPTS_PER_MINUTE", 5)
# Per-account lockout (stored in the database, so it survives restarts/sleep):
# after this many consecutive wrong passwords the account is locked for a while.
LOCKOUT_AFTER_FAILURES = _env_int("LOCKOUT_AFTER_FAILURES", 5)
LOCKOUT_MINUTES = _env_int("LOCKOUT_MINUTES", 15)

# --- Public demo (sandbox) ----------------------------------------------------
# One-click, password-free access for people who open the live link. Each visitor
# gets a private sandbox: their own claims, hidden from everyone else, never used
# for retraining or similar-claims evidence, and purged after DEMO_DATA_TTL_HOURS.
# Off by default; turn it off at any time by unsetting DEMO_ENABLED.
DEMO_ENABLED = _env_bool("DEMO_ENABLED", False)
DEMO_MAX_CLAIMS = _env_int("DEMO_MAX_CLAIMS", 5)  # claims one visitor may file
DEMO_TOKEN_TTL_MINUTES = _env_int("DEMO_TOKEN_TTL_MINUTES", 60)
DEMO_REQUESTS_PER_MINUTE = _env_int("DEMO_REQUESTS_PER_MINUTE", 90)  # per visitor
DEMO_STARTS_PER_HOUR_PER_IP = _env_int("DEMO_STARTS_PER_HOUR_PER_IP", 12)
# Across ALL visitors, so spreading starts over many addresses cannot grow the database unboundedly.
DEMO_STARTS_PER_HOUR_TOTAL = _env_int("DEMO_STARTS_PER_HOUR_TOTAL", 200)
# Shared across ALL visitors: past this, demo claims get the rules-based
# recommendation instead of a Groq call, so the demo can never drain the real budget.
DEMO_AI_CALLS_PER_DAY = _env_int("DEMO_AI_CALLS_PER_DAY", 60)
DEMO_DATA_TTL_HOURS = _env_int("DEMO_DATA_TTL_HOURS", 24)
DEMO_SEED_CLAIMS = _env_int("DEMO_SEED_CLAIMS", 24)  # sample claims that fill the dashboards
# Where visitors who hit a demo limit are sent.
DEMO_CONTACT_URL = os.getenv("DEMO_CONTACT_URL", "https://a-iportfolio-sigma.vercel.app/").strip()

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
# "json" (one event per line, for log aggregators) or "text". JSON in production.
LOG_FORMAT = os.getenv("LOG_FORMAT", "json" if IS_PRODUCTION else "text").strip().lower()


def get_groq_api_key() -> str | None:
    """Return the Groq API key from the environment, or None if unset/blank."""
    key = os.getenv("GROQ_API_KEY", "").strip()
    return key or None


def configure_logging() -> None:
    """Configure root logging once (idempotent). Never log secrets or claim PII."""
    from logging_setup import setup_logging

    setup_logging(LOG_LEVEL, LOG_FORMAT)
