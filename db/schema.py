"""
schema.py

Every table, defined once with SQLAlchemy Core so the same code runs on SQLite
(local development, tests) and Postgres (production, e.g. Neon's free tier).

Two groups, because locally they live in two SQLite files (claims.db and
feedback.db, kept for compatibility with existing data). With DATABASE_URL set,
both groups live in the one Postgres database.

Timestamps are stored as ISO-8601 UTC strings, as they always have been here:
they sort correctly as text and round-trip identically on both databases.
"""

from sqlalchemy import Boolean, Column, Float, Index, Integer, MetaData, String, Table, Text, false, text, true

CLAIMS_META = MetaData()
FEEDBACK_META = MetaData()

claims = Table(
    "claims",
    CLAIMS_META,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("claim_json", Text, nullable=False),
    Column("fraud_result_json", Text),
    Column("similar_claims_json", Text),
    Column("recommendation_json", Text),
    Column("status", String(20), nullable=False, server_default="pending"),
    Column("created_at", String(40), nullable=False),
    Column("reviewed_at", String(40)),
    Column("warnings_json", Text),
    Column("similar_summary", Text),
    Column("submitted_by", String(50)),
    Column("outcome", String(20)),
    # NULL for real claims. "seed" = shared sample data, "template" = hidden source
    # for a new visitor's starter claims, anything else = one demo visitor's private
    # sandbox (see demo.py). Real lists never include sandbox rows.
    Column("demo_session", String(40)),
    Index("idx_claims_submitted_by", "submitted_by"),
    Index("idx_claims_demo_session", "demo_session"),
)

# Accounts created by the organization. The admin is NOT stored here: it comes
# from ADMIN_USERNAME / ADMIN_PASSWORD_HASH, so the database can never lock the
# organization out. ``username_key`` is the case-folded username and carries the
# uniqueness rule ("Carol" and "carol" are the same account).
users = Table(
    "users",
    CLAIMS_META,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("username", String(50), nullable=False),
    Column("username_key", String(50), nullable=False),
    Column("full_name", String(100), nullable=False),
    Column("role", String(20), nullable=False),
    Column("password_hash", Text, nullable=False),
    Column("active", Boolean, nullable=False, server_default=true()),
    Column("profile_json", Text),
    Column("created_at", String(40), nullable=False),
    Column("created_by", String(50)),
    # Set when the organization chose the password (new account, reset): the
    # user must replace it at their next sign-in.
    Column("must_change_password", Boolean, nullable=False, server_default=false()),
    Column("password_changed_at", String(40)),
    Index("ux_users_username_key", "username_key", unique=True),
)

# Failed sign-ins per username, kept in the database (not memory) so a lockout
# survives restarts and a free host going to sleep.
login_throttle = Table(
    "login_throttle",
    CLAIMS_META,
    Column("username_key", String(100), primary_key=True),
    Column("failures", Integer, nullable=False, server_default=text("0")),
    Column("locked_until", String(40)),
)

# Who did what, for the administrator. Never stores passwords, tokens or claim data.
audit_events = Table(
    "audit_events",
    CLAIMS_META,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("at", String(40), nullable=False),
    Column("actor", String(100)),
    Column("action", String(50), nullable=False),
    Column("target", String(200)),
    Column("ip", String(64)),
    Index("idx_audit_at", "at"),
)

feedback = Table(
    "feedback",
    FEEDBACK_META,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("claim_id", Integer),
    Column("claim_json", Text, nullable=False),
    Column("fraud_probability", Float),
    Column("ai_recommendation", Text),
    Column("investigator_decision", String(40), nullable=False),
    Column("confirmed_fraud_reported", String(1), nullable=False),
    Column("created_at", String(40), nullable=False),
    # One feedback row per claim; rows without a claim_id (CLI/smoke tests) are exempt.
    Index(
        "ux_feedback_claim_id",
        "claim_id",
        unique=True,
        sqlite_where=text("claim_id IS NOT NULL"),
        postgresql_where=text("claim_id IS NOT NULL"),
    ),
)
