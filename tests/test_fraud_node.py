"""
tests/test_fraud_node.py

Pytest suite for agents/fraud_node.py.

Run with: uv run pytest
(you may need: uv add --dev pytest   -- to install pytest as a dev dependency first)
"""

import pytest
from agents.fraud_node import fraud_node


@pytest.fixture
def sample_claim():
    """A realistic, fully valid claim — every field within the training distribution."""
    return {
        "months_as_customer": 328,
        "age": 48,
        "policy_state": "OH",
        "policy_csl": "250/500",
        "policy_deductable": 1000,
        "policy_annual_premium": 1406.91,
        "umbrella_limit": 0,
        "insured_sex": "MALE",
        "insured_education_level": "MD",
        "insured_occupation": "craft-repair",
        "insured_relationship": "husband",
        "capital-gains": 53300,
        "capital-loss": 0,
        "incident_type": "Single Vehicle Collision",
        "collision_type": "Side Collision",
        "incident_severity": "Major Damage",
        "authorities_contacted": "Police",
        "incident_state": "SC",
        "incident_city": "Columbus",
        "incident_hour_of_the_day": 5,
        "number_of_vehicles_involved": 1,
        "property_damage": "YES",
        "bodily_injuries": 1,
        "witnesses": 2,
        "police_report_available": "YES",
        "total_claim_amount": 71610,
        "injury_claim": 6510,
        "property_claim": 13020,
        "vehicle_claim": 52080,
        "auto_make": "Saab",
        "auto_model": "92x",
        "auto_year": 2004,
    }


def test_normal_claim_returns_ok_status(sample_claim):
    """A fully valid claim should score successfully."""
    result = fraud_node(sample_claim)
    assert result["status"] == "ok"


def test_normal_claim_has_expected_fields(sample_claim):
    """The result should include probability, signals, and investigation flag."""
    result = fraud_node(sample_claim)
    assert "fraud_probability" in result
    assert 0.0 <= result["fraud_probability"] <= 1.0
    assert isinstance(result["fraud_signals"], list)
    assert isinstance(result["requires_investigation"], bool)


def test_major_damage_claim_flagged_high_risk(sample_claim):
    """Major Damage + collision + high claim amount should score high and get flagged."""
    result = fraud_node(sample_claim)
    assert result["fraud_probability"] > 0.5
    assert result["requires_investigation"] is True
    assert any("Major damage" in s for s in result["fraud_signals"])


def test_unseen_auto_make_is_rejected_gracefully(sample_claim):
    """An auto_make the model never saw during training should not crash —
    it should be routed to manual review instead."""
    claim = dict(sample_claim)
    claim["auto_make"] = "Tesla"  # not in ENCODINGS

    result = fraud_node(claim)

    assert result["status"] == "unrecognized_category"
    assert result["requires_investigation"] is True
    assert "fraud_probability" not in result


def test_out_of_range_total_claim_amount_flagged(sample_claim):
    """A total_claim_amount far outside the training range should still score,
    but come back with a warning and forced investigation."""
    claim = dict(sample_claim)
    claim["total_claim_amount"] = 500_000  # absurdly high vs training data

    result = fraud_node(claim)

    assert result["status"] == "ok"
    assert len(result["out_of_range_warnings"]) > 0
    assert result["requires_investigation"] is True


def test_missing_required_field_is_rejected_gracefully(sample_claim):
    """A claim missing a required field should not crash the graph."""
    claim = dict(sample_claim)
    del claim["incident_severity"]

    result = fraud_node(claim)

    assert result["status"] == "missing_field"
    assert result["requires_investigation"] is True