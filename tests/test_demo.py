"""The public demo: private per-visitor sandboxes, hard limits, and the sample data."""

import jwt
import pytest
from fastapi.testclient import TestClient

import auth
import config
import demo
import main
from agents import feedback_node as feedback_module
from agents import groq_client
from db import claims_store
from resilience import KeyedRateLimiter, SlidingWindowBudget

ADMIN_PW = "admin password 123"
SECRET = "d" * 48


@pytest.fixture(scope="module")
def admin_hash():
    return auth.hash_password(ADMIN_PW)


@pytest.fixture
def client(monkeypatch, admin_hash):
    monkeypatch.setattr(config, "AUTH_DISABLED", False)
    monkeypatch.setattr(config, "ADMIN_USERNAME", "admin")
    monkeypatch.setattr(config, "ADMIN_PASSWORD_HASH", admin_hash)
    monkeypatch.setattr(config, "JWT_SECRET", SECRET)
    monkeypatch.setattr(config, "DEMO_ENABLED", True)
    monkeypatch.setattr(config, "DEMO_MAX_CLAIMS", 2)
    monkeypatch.setattr(main, "_login_limiter", KeyedRateLimiter(1_000))
    monkeypatch.setattr(main, "_claims_limiter", KeyedRateLimiter(1_000))
    monkeypatch.setattr(demo, "request_limiter", KeyedRateLimiter(1_000))
    monkeypatch.setattr(demo, "start_limiter", KeyedRateLimiter(1_000, window_s=3600.0))
    monkeypatch.setattr(demo, "total_start_limiter", KeyedRateLimiter(1_000, window_s=3600.0))
    monkeypatch.setattr(demo, "_ai_budget", SlidingWindowBudget(1_000, 1_000))
    return TestClient(main.app)


@pytest.fixture
def incident(valid_claim):
    return {k: valid_claim[k] for k in (
        "incident_type", "collision_type", "incident_severity", "authorities_contacted",
        "incident_state", "incident_city", "incident_hour_of_the_day", "number_of_vehicles_involved",
        "property_damage", "bodily_injuries", "witnesses", "police_report_available",
        "injury_claim", "property_claim", "vehicle_claim",
    )}


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def start(client, role="investigator"):
    res = client.post("/demo/start", json={"role": role})
    assert res.status_code == 200, res.text
    return _auth(res.json()["access_token"])


def switch(client, headers, role):
    res = client.post("/demo/switch", json={"role": role}, headers=headers)
    assert res.status_code == 200, res.text
    return _auth(res.json()["access_token"])


def admin_headers(client):
    res = client.post("/auth/login", json={"username": "admin", "password": ADMIN_PW})
    assert res.status_code == 200, res.text
    return _auth(res.json()["access_token"])


def seed_row(tag, status="reviewed", outcome="approved"):
    cid = claims_store.insert_claim({"incident_type": "Parked Car"}, {"status": "ok", "fraud_probability": 0.1}, [],
                                    {"recommendation": "approve"}, submitted_by="ava.martinez", demo_session=tag)
    if status == "reviewed":
        claims_store.mark_reviewed(cid, outcome)
    return cid


# ---------------------------------------------------------------------------
# Switch and config
# ---------------------------------------------------------------------------
def test_demo_is_off_by_default_and_invisible(client, monkeypatch):
    monkeypatch.setattr(config, "DEMO_ENABLED", False)
    assert client.get("/auth/config").json() == {"auth_required": True}
    assert client.post("/demo/start", json={"role": "customer"}).status_code == 404


def test_config_advertises_the_demo_when_enabled(client):
    cfg = client.get("/auth/config").json()
    assert cfg["demo"]["enabled"] is True and cfg["demo"]["max_claims"] == 2 and cfg["demo"]["contact_url"]


def test_start_returns_a_token_for_the_chosen_role(client):
    for role in ("investigator", "customer"):
        body = client.post("/demo/start", json={"role": role}).json()
        assert body["user"]["role"] == role and body["user"]["demo"] is True
        assert body["demo"]["claims_used"] == 0 and body["demo"]["claims_max"] == 2
        me = client.get("/auth/me", headers=_auth(body["access_token"])).json()
        assert me["role"] == role and me["demo"] is True


def test_start_rejects_other_roles(client):
    assert client.post("/demo/start", json={"role": "admin"}).status_code == 422


def test_turning_the_demo_off_revokes_existing_tokens(client, monkeypatch):
    headers = start(client)
    monkeypatch.setattr(config, "DEMO_ENABLED", False)
    assert client.get("/auth/me", headers=headers).status_code == 401


def test_a_forged_or_malformed_demo_token_is_rejected(client):
    bad_session = jwt.encode({"sub": "demo:not-a-session", "demo_role": "investigator", "iss": auth.ISSUER,
                              "iat": 1, "exp": 9_999_999_999}, SECRET, "HS256")
    bad_role = jwt.encode({"sub": "demo:" + "a" * 24, "demo_role": "admin", "iss": auth.ISSUER,
                           "iat": 1, "exp": 9_999_999_999}, SECRET, "HS256")
    for token in (bad_session, bad_role):
        assert client.get("/auth/me", headers=_auth(token)).status_code == 401


# ---------------------------------------------------------------------------
# What a demo visitor can and cannot do
# ---------------------------------------------------------------------------
def test_a_demo_visitor_cannot_manage_accounts_or_read_the_log(client):
    headers = start(client)
    assert client.get("/users", headers=headers).status_code == 403
    assert client.get("/audit", headers=headers).status_code == 403
    body = {"username": "mallory", "full_name": "M", "password": "a long password 1", "role": "customer"}
    assert client.post("/users", json=body, headers=headers).status_code == 403
    assert client.post("/users/1/active", json={"active": False}, headers=headers).status_code == 403
    assert client.post("/users/1/password", json={"password": "a long password 1"}, headers=headers).status_code == 403
    change = {"current_password": "x", "new_password": "a long password 1"}
    assert client.post("/auth/password", json=change, headers=headers).status_code == 403


def test_demo_endpoints_refuse_real_accounts(client):
    admin = admin_headers(client)
    assert client.get("/demo/status", headers=admin).status_code == 403
    assert client.post("/demo/switch", json={"role": "customer"}, headers=admin).status_code == 403


def test_a_demo_customer_cannot_use_organization_routes(client):
    headers = start(client, "customer")
    assert client.get("/claims", headers=headers).status_code == 403
    assert client.get("/users", headers=headers).status_code == 403


# ---------------------------------------------------------------------------
# The sandbox: isolation, the customer -> investigator round trip, no feedback
# ---------------------------------------------------------------------------
def test_full_round_trip_in_one_sandbox_without_touching_feedback(client, incident):
    customer = start(client, "customer")
    assert client.get("/my/policy", headers=customer).json()["policy_state"] == demo.DEMO_PROFILE["policy_state"]

    filed = client.post("/my/claims", json=incident, headers=customer)
    assert filed.status_code == 200, filed.text
    claim_id = filed.json()["id"]
    assert filed.json()["status"] == "under_review"
    assert not {"fraud_result", "recommendation"} & set(filed.json())  # customers never see the analysis

    investigator = switch(client, customer, "investigator")
    listing = client.get("/claims", headers=investigator).json()
    assert claim_id in {c["id"] for c in listing}
    detail = client.get(f"/claims/{claim_id}", headers=investigator).json()
    assert detail["fraud_result"] is not None and detail["recommendation"] is not None

    fb = {"investigator_decision": "agreed", "confirmed_fraud_reported": "N"}
    reviewed = client.post(f"/claims/{claim_id}/feedback", json=fb, headers=investigator)
    assert reviewed.status_code == 200 and reviewed.json()["demo"] is True

    back = switch(client, investigator, "customer")
    mine = {c["id"]: c for c in client.get("/my/claims", headers=back).json()}
    assert mine[claim_id]["status"] == "approved"

    # The decision never became training data or similar-claims evidence.
    assert feedback_module.load_indexable_feedback() == []
    # ...and a second review is still a conflict.
    assert client.post(f"/claims/{claim_id}/feedback", json=fb, headers=investigator).status_code == 409


def test_two_visitors_never_see_each_others_claims(client, incident):
    a, b = start(client, "customer"), start(client, "customer")
    a_id = client.post("/my/claims", json=incident, headers=a).json()["id"]

    assert client.get("/my/claims", headers=b).json() == []
    b_org = switch(client, b, "investigator")
    assert a_id not in {c["id"] for c in client.get("/claims", headers=b_org).json()}
    assert client.get(f"/claims/{a_id}", headers=b_org).status_code == 404
    fb = {"investigator_decision": "agreed", "confirmed_fraud_reported": "N"}
    assert client.post(f"/claims/{a_id}/feedback", json=fb, headers=b_org).status_code == 404


def test_an_investigator_demo_can_file_a_full_claim(client, valid_claim):
    headers = start(client)
    res = client.post("/claims", json=valid_claim, headers=headers)
    assert res.status_code == 200, res.text
    assert res.json()["id"] in {c["id"] for c in client.get("/claims", headers=headers).json()}


def test_real_staff_never_see_sandbox_claims_but_do_see_the_samples(client, incident, valid_claim):
    sample = seed_row(claims_store.SEED)
    template = seed_row(claims_store.TEMPLATE, status="pending")
    real = claims_store.insert_claim({"incident_type": "Real"}, {}, [], {}, submitted_by="carol")
    sandbox = client.post("/my/claims", json=incident, headers=start(client, "customer")).json()["id"]

    admin = admin_headers(client)
    seen = {c["id"] for c in client.get("/claims", headers=admin).json()}
    assert {sample, real} <= seen and sandbox not in seen and template not in seen
    assert client.get(f"/claims/{sandbox}", headers=admin).status_code == 404
    assert client.get(f"/claims/{template}", headers=admin).status_code == 404


def test_sample_claims_are_visible_but_read_only_to_a_visitor(client):
    pending_sample = seed_row(claims_store.SEED, status="pending")
    headers = start(client)
    assert pending_sample in {c["id"] for c in client.get("/claims", headers=headers).json()}
    fb = {"investigator_decision": "agreed", "confirmed_fraud_reported": "N"}
    assert client.post(f"/claims/{pending_sample}/feedback", json=fb, headers=headers).status_code == 403


def test_a_new_sandbox_gets_the_starter_claims_and_they_do_not_count_toward_the_cap(client):
    seed_row(claims_store.TEMPLATE, status="pending")
    seed_row(claims_store.TEMPLATE, status="pending")
    headers = start(client)
    starters = [c for c in client.get("/claims", headers=headers).json() if c["status"] == "pending"]
    assert len(starters) == 2
    assert client.get("/demo/status", headers=headers).json()["claims_used"] == 0
    # and a customer in the same sandbox sees them as their own claims, under review
    mine = client.get("/my/claims", headers=switch(client, headers, "customer")).json()
    assert len(mine) == 2 and {c["status"] for c in mine} == {"under_review"}


def test_the_hidden_templates_themselves_stay_pending_for_the_next_visitor(client):
    seed_row(claims_store.TEMPLATE, status="pending")
    first = start(client)
    fid = next(c["id"] for c in client.get("/claims", headers=first).json() if c["status"] == "pending")
    fb = {"investigator_decision": "agreed", "confirmed_fraud_reported": "N"}
    assert client.post(f"/claims/{fid}/feedback", json=fb, headers=first).status_code == 200
    second = start(client)
    assert [c["status"] for c in client.get("/claims", headers=second).json()] == ["pending"]


# ---------------------------------------------------------------------------
# Limits
# ---------------------------------------------------------------------------
def test_claim_cap_returns_a_friendly_429_with_the_contact_link(client, incident):
    headers = start(client, "customer")
    for _ in range(2):
        assert client.post("/my/claims", json=incident, headers=headers).status_code == 200
    res = client.post("/my/claims", json=incident, headers=headers)
    assert res.status_code == 429
    detail = res.json()["detail"]
    assert detail["code"] == "demo_limit" and detail["kind"] == "claims"
    assert detail["contact_url"] == config.DEMO_CONTACT_URL and "portfolio" in detail["message"]
    assert client.get("/demo/status", headers=headers).json()["claims_used"] == 2


def test_the_cap_covers_both_roles_and_is_per_visitor(client, incident, valid_claim):
    a = start(client, "customer")
    client.post("/my/claims", json=incident, headers=a)
    a_org = switch(client, a, "investigator")
    assert client.post("/claims", json=valid_claim, headers=a_org).status_code == 200
    assert client.post("/claims", json=valid_claim, headers=a_org).status_code == 429
    # another visitor is unaffected
    assert client.post("/my/claims", json=incident, headers=start(client, "customer")).status_code == 200


def test_per_visitor_request_rate_limit(client, monkeypatch):
    monkeypatch.setattr(demo, "request_limiter", KeyedRateLimiter(3))
    headers = start(client)
    codes = [client.get("/auth/me", headers=headers).status_code for _ in range(5)]
    assert codes[:3] == [200, 200, 200] and codes[3] == 429
    limited = client.get("/auth/me", headers=headers)
    assert limited.json()["detail"]["kind"] == "rate" and "retry-after" in limited.headers
    assert client.get("/auth/me", headers=start(client)).status_code == 200  # a different visitor


def test_starting_demos_is_rate_limited_per_ip(client, monkeypatch):
    monkeypatch.setattr(demo, "start_limiter", KeyedRateLimiter(2, window_s=3600.0))
    assert [client.post("/demo/start", json={"role": "customer"}).status_code for _ in range(3)] == [200, 200, 429]


def test_starting_demos_is_rate_limited_overall(client, monkeypatch):
    monkeypatch.setattr(demo, "total_start_limiter", KeyedRateLimiter(2, window_s=3600.0))
    assert [client.post("/demo/start", json={"role": "customer"}).status_code for _ in range(3)] == [200, 200, 429]


def test_the_shared_ai_allowance_switches_demo_claims_to_rules_and_never_calls_groq(client, incident, monkeypatch):
    monkeypatch.setattr(demo, "_ai_budget", SlidingWindowBudget(10, 0))  # nothing left today
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test_not_real")

    def boom(*_a, **_k):
        raise AssertionError("Groq must not be called once the demo allowance is used up")

    monkeypatch.setattr(groq_client, "chat", boom)
    headers = start(client, "customer")
    claim_id = client.post("/my/claims", json=incident, headers=headers).json()["id"]
    detail = client.get(f"/claims/{claim_id}", headers=switch(client, headers, "investigator")).json()
    assert detail["recommendation"]["fallback_reason"] == "demo_ai_limit"
    assert any("allowance" in w for w in detail["warnings"])


def test_real_claims_are_not_limited_by_the_demo_allowance(client, valid_claim, monkeypatch):
    monkeypatch.setattr(demo, "_ai_budget", SlidingWindowBudget(10, 0))
    res = client.post("/claims", json=valid_claim, headers=admin_headers(client))
    assert res.status_code == 200
    assert res.json()["recommendation"]["fallback_reason"] != "demo_ai_limit"


# ---------------------------------------------------------------------------
# Cleanup and seeding
# ---------------------------------------------------------------------------
def test_purge_removes_sandboxes_but_never_samples_or_templates():
    sample = seed_row(claims_store.SEED)
    template = seed_row(claims_store.TEMPLATE, status="pending")
    real = claims_store.insert_claim({}, {}, [], {})
    sandbox = seed_row("a" * 24)
    assert claims_store.purge_demo_sandboxes("9999") == 1
    assert claims_store.get_claim(sandbox) is None
    assert all(claims_store.get_claim(i) for i in (sample, template, real))


def test_delete_demo_data_can_keep_the_samples():
    sample, sandbox = seed_row(claims_store.SEED), seed_row("b" * 24)
    assert claims_store.delete_demo_data(include_seed=False) == 1
    assert claims_store.get_claim(sandbox) is None and claims_store.get_claim(sample)
    claims_store.delete_demo_data(include_seed=True)
    assert claims_store.get_claim(sample) is None


def test_ensure_seed_builds_reviewed_samples_and_pending_templates_once(monkeypatch):
    monkeypatch.setattr(config, "DEMO_SEED_CLAIMS", 4)
    assert demo.ensure_seed(pace_s=0) == "seeded"

    samples = [c for c in claims_store.list_claims() if c["demo_session"] == claims_store.SEED]
    assert len(samples) == 4
    assert all(c["status"] == "reviewed" and c["outcome"] in ("approved", "not_approved") for c in samples)
    assert all(c["reviewed_at"] and c["reviewed_at"] >= c["created_at"] for c in samples)
    assert all(c["fraud_result"]["status"] == "ok" and c["recommendation"] for c in samples)
    assert len({c["submitted_by"] for c in samples}) == 4

    assert 1 <= claims_store.count_tagged(claims_store.TEMPLATE) <= 3
    assert feedback_module.load_indexable_feedback() == []  # samples are not training data

    assert demo.ensure_seed(pace_s=0) == "exists"  # idempotent
    assert claims_store.count_tagged(claims_store.SEED) == 4


def test_a_failing_seed_writes_nothing(monkeypatch):
    monkeypatch.setattr(config, "DEMO_SEED_CLAIMS", 3)

    def broken(*_a, **_k):
        raise RuntimeError("pipeline exploded")

    monkeypatch.setattr("graph.build_graph", broken)
    with pytest.raises(RuntimeError):
        demo.ensure_seed(pace_s=0)
    assert claims_store.count_tagged(claims_store.SEED) == 0
    assert claims_store.count_tagged(claims_store.TEMPLATE) == 0
