"""HTTP-level tests: validation (422), limits (429), idempotent feedback (409), degradation."""

import pytest
from fastapi.testclient import TestClient

import main
from resilience import KeyedRateLimiter


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main, "_claims_limiter", KeyedRateLimiter(1_000))
    return TestClient(main.app)


def _submit(client, claim):
    return client.post("/claims", json=claim)


def test_health_reports_status_without_secrets(client, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-super-secret")
    body = client.get("/health").json()
    assert body["status"] == "ok" and body["fraud_model_loaded"] is True
    assert body["llm"]["configured"] is True
    assert "gsk-super-secret" not in client.get("/health").text


def test_submit_claim_runs_pipeline_and_persists(client, valid_claim):
    response = _submit(client, valid_claim)
    assert response.status_code == 200
    body = response.json()
    assert body["recommendation"]["source"] == "rules_fallback"  # no key in tests
    assert body["warnings"]  # degradations are reported
    detail = client.get(f"/claims/{body['id']}").json()
    assert detail["status"] == "pending" and detail["warnings"] == body["warnings"]


def test_unseen_category_is_accepted_and_routed_to_manual_review(client, valid_claim):
    valid_claim["auto_make"] = "Tesla"
    body = _submit(client, valid_claim).json()
    assert body["fraud_result"]["status"] == "unrecognized_category"
    assert body["recommendation"]["recommendation"] == "investigate"


@pytest.mark.parametrize(
    "field,value",
    [
        ("age", -1),
        ("age", 10_000),
        ("incident_hour_of_the_day", 99),
        ("total_claim_amount", -5),
        ("auto_make", "x" * 500),
        ("auto_make", ""),
    ],
)
def test_out_of_bounds_input_is_rejected_with_422(client, valid_claim, field, value):
    valid_claim[field] = value
    assert _submit(client, valid_claim).status_code == 422


def test_unknown_fields_are_rejected(client, valid_claim):
    valid_claim["__proto__"] = "x"
    assert _submit(client, valid_claim).status_code == 422


def test_claim_submission_is_rate_limited_per_client(valid_claim, monkeypatch):
    monkeypatch.setattr(main, "_claims_limiter", KeyedRateLimiter(2))
    client = TestClient(main.app)
    assert _submit(client, valid_claim).status_code == 200
    assert _submit(client, valid_claim).status_code == 200
    third = _submit(client, valid_claim)
    assert third.status_code == 429 and int(third.headers["Retry-After"]) >= 1


def test_claim_list_is_paginated(client, valid_claim):
    for _ in range(3):
        _submit(client, valid_claim)
    assert len(client.get("/claims?limit=2").json()) == 2
    assert len(client.get("/claims?limit=2&offset=2").json()) == 1
    assert client.get("/claims?limit=0").status_code == 422
    assert client.get("/claims?limit=100000").status_code == 422


def test_unknown_claim_is_404(client):
    assert client.get("/claims/999").status_code == 404
    feedback = {"investigator_decision": "agreed", "confirmed_fraud_reported": "N"}
    assert client.post("/claims/999/feedback", json=feedback).status_code == 404


def test_feedback_is_accepted_once_then_409(client, valid_claim):
    claim_id = _submit(client, valid_claim).json()["id"]
    feedback = {"investigator_decision": "agreed", "confirmed_fraud_reported": "Y"}
    first = client.post(f"/claims/{claim_id}/feedback", json=feedback)
    assert first.status_code == 200 and first.json()["status"] == "ok"
    assert client.post(f"/claims/{claim_id}/feedback", json=feedback).status_code == 409
    assert client.get(f"/claims/{claim_id}").json()["status"] == "reviewed"


def test_feedback_succeeds_even_if_the_index_is_down(client, valid_claim):
    claim_id = _submit(client, valid_claim).json()["id"]
    feedback = {"investigator_decision": "agreed", "confirmed_fraud_reported": "N"}
    body = client.post(f"/claims/{claim_id}/feedback", json=feedback).json()
    assert body["indexed"] is False  # index stubbed as unavailable; feedback still saved


@pytest.mark.parametrize(
    "payload",
    [
        {"investigator_decision": "whatever", "confirmed_fraud_reported": "Y"},
        {"investigator_decision": "agreed", "confirmed_fraud_reported": "maybe"},
        {"investigator_decision": "agreed"},
    ],
)
def test_invalid_feedback_is_422_not_500(client, valid_claim, payload):
    claim_id = _submit(client, valid_claim).json()["id"]
    assert client.post(f"/claims/{claim_id}/feedback", json=payload).status_code == 422


def test_fraud_rate_summary_is_returned_and_persisted(client, valid_claim, monkeypatch):
    from retrieval import retrieval_node as rn
    from retrieval.similarity import SimilarityIndex

    history = [dict(valid_claim, fraud_reported="Y" if i < 3 else "N", total_claim_amount=70000 + i) for i in range(20)]
    index = SimilarityIndex(history)
    monkeypatch.setattr(rn, "get_index", lambda: index)
    body = client.post("/claims", json=valid_claim).json()
    assert body["similar_claims_summary"] == "3 of the 15 most similar past claims were confirmed fraud."
    assert len(body["similar_claims"]) == 5 and "matched_fields" in body["similar_claims"][0]
    assert client.get(f"/claims/{body['id']}").json()["similar_claims_summary"] == body["similar_claims_summary"]
