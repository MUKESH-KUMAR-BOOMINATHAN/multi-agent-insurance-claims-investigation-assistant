"""
Shared fixtures. Every test runs against throwaway SQLite files, with no Groq
key, a fresh budget/breaker/cache, and the similar-claims index stubbed as "not built"
(so pipeline tests exercise the fallback path and never touch the network).

To run the whole suite against Postgres instead, point TEST_DATABASE_URL at an
EMPTY, disposable database (every table is truncated before each test):

    TEST_DATABASE_URL=postgresql://user:pw@host:5432/test_db  python -m pytest
"""

import os

import pytest
from sqlalchemy import text

import config
from agents import feedback_node as feedback_module
from agents import groq_client, recommendation_node
from db import claims_store
from db import engine as db_engine
from retrieval import retrieval_node, similarity


TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "").strip() or None
postgres_only = pytest.mark.skipif(not TEST_DATABASE_URL, reason="needs TEST_DATABASE_URL")
sqlite_only = pytest.mark.skipif(bool(TEST_DATABASE_URL), reason="SQLite-specific")


@pytest.fixture
def valid_claim() -> dict:
    """A realistic, fully valid claim — every field within the training distribution."""
    return {
        "months_as_customer": 328, "age": 48, "policy_state": "OH", "policy_csl": "250/500",
        "policy_deductable": 1000, "policy_annual_premium": 1406.91, "umbrella_limit": 0,
        "insured_sex": "MALE", "insured_education_level": "MD", "insured_occupation": "craft-repair",
        "insured_relationship": "husband", "capital-gains": 53300, "capital-loss": 0,
        "incident_type": "Single Vehicle Collision", "collision_type": "Side Collision",
        "incident_severity": "Major Damage", "authorities_contacted": "Police",
        "incident_state": "SC", "incident_city": "Columbus", "incident_hour_of_the_day": 5,
        "number_of_vehicles_involved": 1, "property_damage": "YES", "bodily_injuries": 1,
        "witnesses": 2, "police_report_available": "YES", "total_claim_amount": 71610,
        "injury_claim": 6510, "property_claim": 13020, "vehicle_claim": 52080,
        "auto_make": "Saab", "auto_model": "92x", "auto_year": 2004,
    }


@pytest.fixture(autouse=True)
def isolated_environment(tmp_path, monkeypatch):
    """Redirect storage, clear secrets, reset guards, stub the vector index."""
    monkeypatch.setattr(claims_store, "DB_PATH", tmp_path / "claims.db")
    monkeypatch.setattr(feedback_module, "DB_PATH", tmp_path / "feedback.db")
    monkeypatch.setattr(claims_store, "_initialized_for", None)
    monkeypatch.setattr(feedback_module, "_initialized_for", None)
    monkeypatch.setattr(config, "DATABASE_URL", TEST_DATABASE_URL)
    db_engine.reset_for_tests()
    if TEST_DATABASE_URL:
        with claims_store._connect() as conn:  # creates the schema on first use
            conn.execute(text(
                "TRUNCATE claims, users, login_throttle, audit_events RESTART IDENTITY"
            ))
        with feedback_module._connect() as conn:
            conn.execute(text("TRUNCATE feedback RESTART IDENTITY"))

    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    groq_client.reset_guards()
    recommendation_node.clear_cache()

    def _no_index():
        raise similarity.IndexNotBuiltError("stubbed in tests")

    # Pipeline tests run WITHOUT similar-claims evidence (the fallback path);
    # tests/test_similarity.py exercises the real index.
    similarity.reset_index()
    monkeypatch.setattr(retrieval_node, "get_index", _no_index)
    monkeypatch.setattr(similarity, "get_index", _no_index)
    # Existing API tests exercise the pipeline, not auth; tests/test_auth.py turns it on.
    monkeypatch.setattr(config, "AUTH_DISABLED", True)
    monkeypatch.setattr(config, "IS_PRODUCTION", False)
    # Make retries instant.
    monkeypatch.setattr(groq_client.time, "sleep", lambda _s: None)
    monkeypatch.setattr(config, "GROQ_MAX_RETRIES", 1)
    yield
