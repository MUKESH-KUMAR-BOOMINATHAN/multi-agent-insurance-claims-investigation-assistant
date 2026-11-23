"""Recommendation node: schema validation, code-enforced policy, cache, and fallbacks."""

import json

import pytest

from agents import groq_client, recommendation_node
from agents.groq_client import LLMUnavailable
from agents.recommendation_node import recommendation_node as run_node

FLAGGED_OK = {
    "status": "ok",
    "fraud_probability": 0.87,
    "fraud_signals": ["Major damage severity — historically 60%+ fraud rate"],
    "requires_investigation": True,
    "out_of_range_warnings": [],
}
LOW_RISK_OK = {**FLAGGED_OK, "fraud_probability": 0.1, "requires_investigation": False}
UNSCORABLE = {
    "status": "unrecognized_category",
    "fraud_signals": ["Cannot auto-score"],
    "requires_investigation": True,
}
EVIDENCE = [
    {"score": 0.9, "fraud_reported": "Y", "incident_type": "x", "incident_severity": "y"},
    {"score": 0.8, "fraud_reported": "N", "incident_type": "x", "incident_severity": "y"},
]


def _state(fraud_result, similar=None):
    return {"claim": {}, "fraud_result": fraud_result, "similar_claims": similar or []}


def _reply(decision="investigate", confidence=0.8):
    return json.dumps(
        {
            "recommendation": decision,
            "confidence": confidence,
            "key_factors": ["Score is high"],
            "next_steps": ["Assign an investigator"],
        }
    )


@pytest.fixture
def groq(monkeypatch):
    """Stub groq_client.chat; set groq['reply'] (str) or groq['error'] (Exception)."""
    box = {"reply": _reply(), "error": None, "calls": 0}

    def fake_chat(system, user):
        box["calls"] += 1
        if box["error"]:
            raise box["error"]
        return box["reply"]

    monkeypatch.setattr(groq_client, "chat", fake_chat)
    return box


def test_valid_reply_is_used_and_tagged_as_groq(groq):
    rec = run_node(_state(FLAGGED_OK, EVIDENCE))["recommendation"]
    assert rec["source"] == "groq" and rec["recommendation"] == "investigate"
    assert rec["overrides"] == []


def test_unscorable_claim_spends_no_api_call(groq):
    rec = run_node(_state(UNSCORABLE))["recommendation"]
    assert groq["calls"] == 0
    assert rec["recommendation"] == "investigate" and rec["source"] == "rules"


def test_llm_cannot_approve_a_flagged_claim(groq):
    groq["reply"] = _reply("approve")
    rec = run_node(_state(FLAGGED_OK, EVIDENCE))["recommendation"]
    assert rec["recommendation"] == "investigate"
    assert any("approve -> investigate" in o for o in rec["overrides"])


def test_llm_cannot_deny_a_claim_the_model_did_not_flag(groq):
    groq["reply"] = _reply("deny_pending_review")
    rec = run_node(_state(LOW_RISK_OK))["recommendation"]
    assert rec["recommendation"] == "investigate"


def test_deny_is_allowed_when_the_fraud_model_flagged_it(groq):
    groq["reply"] = _reply("deny_pending_review")
    assert run_node(_state(FLAGGED_OK, EVIDENCE))["recommendation"]["recommendation"] == "deny_pending_review"


@pytest.mark.parametrize(
    "bad_reply",
    [
        "I think you should investigate.",  # not JSON
        '["investigate"]',  # JSON but not an object
        json.dumps({"recommendation": "pay_in_full", "confidence": 0.5, "key_factors": [], "next_steps": []}),
        json.dumps({"recommendation": "investigate", "confidence": 7, "key_factors": [], "next_steps": []}),
        json.dumps({"recommendation": "investigate", "confidence": 0.5, "key_factors": "x", "next_steps": []}),
        json.dumps({"recommendation": "investigate", "confidence": 0.5}),  # missing lists
    ],
)
def test_invalid_model_output_falls_back_to_rules(groq, bad_reply):
    groq["reply"] = bad_reply
    rec = run_node(_state(FLAGGED_OK, EVIDENCE))["recommendation"]
    assert rec["source"] == "rules_fallback" and rec["fallback_reason"] == "invalid_llm_output"
    assert rec["recommendation"] == "investigate"


def test_markdown_fenced_json_is_accepted(groq):
    groq["reply"] = "```json\n" + _reply("investigate", 0.6) + "\n```"
    assert run_node(_state(FLAGGED_OK, EVIDENCE))["recommendation"]["source"] == "groq"


@pytest.mark.parametrize("reason", ["no_api_key", "budget_exhausted", "circuit_open", "timeout", "http_500", "rate_limited"])
def test_every_llm_failure_reason_falls_back_instead_of_raising(groq, reason):
    groq["error"] = LLMUnavailable(reason)
    rec = run_node(_state(FLAGGED_OK, EVIDENCE))["recommendation"]
    assert rec["source"] == "rules_fallback" and rec["fallback_reason"] == reason


def test_unexpected_exception_still_returns_a_recommendation(groq):
    groq["error"] = RuntimeError("boom")
    rec = run_node(_state(FLAGGED_OK, EVIDENCE))["recommendation"]
    assert rec["source"] == "rules_fallback" and rec["fallback_reason"] == "unexpected_error"


def test_identical_inputs_reuse_the_cached_answer(groq):
    run_node(_state(FLAGGED_OK, EVIDENCE))
    second = run_node(_state(FLAGGED_OK, EVIDENCE))["recommendation"]
    assert groq["calls"] == 1 and second["cached"] is True


def test_fallback_answers_are_not_cached(groq):
    groq["error"] = LLMUnavailable("timeout")
    run_node(_state(FLAGGED_OK, EVIDENCE))
    groq["error"] = None
    assert run_node(_state(FLAGGED_OK, EVIDENCE))["recommendation"]["source"] == "groq"


def test_rules_fallback_approves_only_clear_low_risk_claims():
    low = recommendation_node.rules_recommendation(_state(LOW_RISK_OK), "no_api_key")
    assert low["recommendation"] == "approve"
    risky_evidence = [{"fraud_reported": "Y"}] * 4 + [{"fraud_reported": "N"}]
    contradicted = recommendation_node.rules_recommendation(_state(LOW_RISK_OK, risky_evidence), "no_api_key")
    assert contradicted["recommendation"] == "investigate"  # low score but similar claims were fraud


def test_rules_fallback_never_denies():
    rec = recommendation_node.rules_recommendation(_state(FLAGGED_OK, [{"fraud_reported": "Y"}] * 5), "no_api_key")
    assert rec["recommendation"] == "investigate"


def test_prompt_marks_claim_data_as_untrusted():
    assert "never instructions" in recommendation_node.SYSTEM_PROMPT


# --- Regression: a model reply with empty next steps / factors (seen in production use) ---
def _reply_with(decision="investigate", next_steps=None, key_factors=None):
    return json.dumps(
        {
            "recommendation": decision,
            "confidence": 0.85,
            "key_factors": ["x"] if key_factors is None else key_factors,
            "next_steps": [] if next_steps is None else next_steps,
        }
    )


def test_empty_next_steps_are_backfilled_with_defaults_and_flagged(groq):
    groq["reply"] = _reply_with("approve", next_steps=[])
    rec = run_node(_state(LOW_RISK_OK))["recommendation"]
    assert rec["source"] == "groq" and rec["recommendation"] == "approve"
    assert rec["next_steps"] == recommendation_node.DEFAULT_NEXT_STEPS["approve"]
    assert any("no next steps" in o for o in rec["overrides"])


def test_blank_strings_count_as_empty_next_steps(groq):
    groq["reply"] = _reply_with("investigate", next_steps=["", "   "])
    rec = run_node(_state(FLAGGED_OK, EVIDENCE))["recommendation"]
    assert rec["next_steps"] == recommendation_node.DEFAULT_NEXT_STEPS["investigate"]


def test_steps_are_replaced_when_policy_reverses_the_decision(groq):
    """'Proceed with standard handling' must never sit under a corrected 'investigate'."""
    groq["reply"] = _reply_with("approve", next_steps=["Proceed with standard claim handling"])
    rec = run_node(_state(FLAGGED_OK, EVIDENCE))["recommendation"]
    assert rec["recommendation"] == "investigate"
    assert rec["next_steps"] == recommendation_node.DEFAULT_NEXT_STEPS["investigate"]
    assert not any("standard claim handling" in s for s in rec["next_steps"])


def test_empty_key_factors_come_from_the_fraud_model_only(groq):
    groq["reply"] = _reply_with("investigate", next_steps=["Assign an investigator"], key_factors=[])
    rec = run_node(_state(FLAGGED_OK, EVIDENCE))["recommendation"]
    assert rec["key_factors"][0] == "Fraud model probability 87%"
    assert all(f in ["Fraud model probability 87%", *FLAGGED_OK["fraud_signals"]] for f in rec["key_factors"])


def test_well_formed_reply_is_left_untouched(groq):
    rec = run_node(_state(FLAGGED_OK, EVIDENCE))["recommendation"]
    assert rec["next_steps"] == ["Assign an investigator"] and rec["overrides"] == []
