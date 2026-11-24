"""Roles: admin / investigator / customer accounts, the customer portal and what each role may see."""

import pytest
from fastapi.testclient import TestClient

import auth
import config
import main
from resilience import KeyedRateLimiter

ADMIN_PW = "admin password 123"
SECRET = "r" * 48
FRAUD_KEYS = {"fraud_result", "fraud_probability", "recommendation", "similar_claims",
              "similar_claims_summary", "warnings", "claim"}


@pytest.fixture(scope="module")
def admin_hash():
    return auth.hash_password(ADMIN_PW)


@pytest.fixture
def client(monkeypatch, admin_hash):
    monkeypatch.setattr(config, "AUTH_DISABLED", False)
    monkeypatch.setattr(config, "ADMIN_USERNAME", "admin")
    monkeypatch.setattr(config, "ADMIN_PASSWORD_HASH", admin_hash)
    monkeypatch.setattr(config, "JWT_SECRET", SECRET)
    monkeypatch.setattr(main, "_login_limiter", KeyedRateLimiter(1_000))
    monkeypatch.setattr(main, "_claims_limiter", KeyedRateLimiter(1_000))
    return TestClient(main.app)


@pytest.fixture
def profile(valid_claim):
    return {k: valid_claim[k] for k in (
        "months_as_customer", "age", "policy_state", "policy_csl", "policy_deductable",
        "policy_annual_premium", "umbrella_limit", "insured_sex", "insured_education_level",
        "insured_occupation", "insured_relationship", "capital-gains", "capital-loss",
        "auto_make", "auto_model", "auto_year",
    )}


@pytest.fixture
def incident(valid_claim):
    return {k: valid_claim[k] for k in (
        "incident_type", "collision_type", "incident_severity", "authorities_contacted",
        "incident_state", "incident_city", "incident_hour_of_the_day", "number_of_vehicles_involved",
        "property_damage", "bodily_injuries", "witnesses", "police_report_available",
        "injury_claim", "property_claim", "vehicle_claim",
    )}


def _headers(client, username, password):
    res = client.post("/auth/login", json={"username": username, "password": password})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


@pytest.fixture
def admin(client):
    return _headers(client, "admin", ADMIN_PW)


TEMP = "temporary pass 1"


def _create(client, headers, username, role, profile=None, password="a long password 1", first_login=True):
    """Create an account with a temporary password; by default also do the user's
    first sign-in and replace it with ``password`` (as the real flow requires)."""
    body = {"username": username, "full_name": username.title(), "password": TEMP, "role": role}
    if password != "a long password 1" and not first_login:
        body["password"] = password
    if profile is not None:
        body["profile"] = profile
    res = client.post("/users", json=body, headers=headers)
    if res.status_code == 201 and first_login:
        temp = _headers(client, username, TEMP)
        changed = client.post("/auth/password", json={"current_password": TEMP, "new_password": password}, headers=temp)
        assert changed.status_code == 204, changed.text
    return res


@pytest.fixture
def investigator(client, admin):
    assert _create(client, admin, "ivy", "investigator").status_code == 201
    return _headers(client, "ivy", "a long password 1")


@pytest.fixture
def customer(client, admin, profile):
    assert _create(client, admin, "carol", "customer", profile).status_code == 201
    return _headers(client, "carol", "a long password 1")


# --- login and identity -----------------------------------------------------
def test_login_returns_the_role_and_me_reflects_it(client, admin, profile):
    _create(client, admin, "dave", "customer", profile)
    res = client.post("/auth/login", json={"username": "dave", "password": "a long password 1"})
    assert res.json()["user"] == {"username": "dave", "role": "customer", "full_name": "Dave",
                                  "must_change_password": False}
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {res.json()['access_token']}"})
    assert me.json()["role"] == "customer"
    assert client.get("/auth/me", headers=admin).json()["role"] == "admin"


# --- account creation rules -------------------------------------------------
def test_admin_creates_investigators_and_customers(client, admin, profile):
    assert _create(client, admin, "ivan", "investigator").status_code == 201
    created = _create(client, admin, "cleo", "customer", profile).json()
    assert created["role"] == "customer" and created["profile"]["auto_make"] == profile["auto_make"]
    assert "password_hash" not in created and "password" not in created


def test_investigator_may_create_customers_but_not_investigators(client, investigator, profile):
    assert _create(client, investigator, "cust1", "customer", profile).status_code == 201
    assert _create(client, investigator, "staff2", "investigator").status_code == 403


def test_customer_may_not_manage_accounts(client, customer, profile):
    assert _create(client, customer, "sneaky", "customer", profile).status_code == 403
    assert client.get("/users", headers=customer).status_code == 403


def test_customer_needs_a_profile_and_staff_must_not_have_one(client, admin, profile):
    assert _create(client, admin, "noprof", "customer").status_code == 422
    assert _create(client, admin, "staffprof", "investigator", profile).status_code == 422


def test_usernames_are_unique_case_insensitively_and_admin_is_reserved(client, admin, profile):
    assert _create(client, admin, "Erin", "customer", profile).status_code == 201
    assert _create(client, admin, "erin", "customer", profile).status_code == 409
    assert _create(client, admin, "ADMIN", "investigator").status_code == 409


@pytest.mark.parametrize("username,password", [("ab", "a long password 1"), ("bad name", "a long password 1"),
                                               ("okname", "short")])
def test_weak_passwords_and_bad_usernames_are_rejected(client, admin, profile, username, password):
    res = _create(client, admin, username, "customer", profile, password=password, first_login=False)
    assert res.status_code == 422


def test_investigator_only_sees_customers_in_the_user_list(client, admin, investigator, profile):
    _create(client, admin, "cus", "customer", profile)
    roles = {u["role"] for u in client.get("/users", headers=investigator).json()}
    assert roles == {"customer"}
    assert {u["role"] for u in client.get("/users", headers=admin).json()} == {"customer", "investigator"}


# --- separation of what each role can see ----------------------------------
def test_customer_cannot_reach_organization_routes(client, customer, valid_claim):
    assert client.get("/claims", headers=customer).status_code == 403
    assert client.get("/claims/1", headers=customer).status_code == 403
    assert client.post("/claims", json=valid_claim, headers=customer).status_code == 403


def test_organization_cannot_use_the_customer_portal(client, admin, incident):
    assert client.post("/my/claims", json=incident, headers=admin).status_code == 403
    assert client.get("/my/claims", headers=admin).status_code == 403


def test_customer_files_a_claim_and_sees_only_a_plain_status(client, customer, incident):
    res = client.post("/my/claims", json=incident, headers=customer)
    assert res.status_code == 200, res.text
    view = res.json()
    assert view["status"] == "under_review"
    assert not FRAUD_KEYS & set(view)
    # The total is computed by the server from the three parts.
    assert view["total_claim_amount"] == incident["injury_claim"] + incident["property_claim"] + incident["vehicle_claim"]


def test_customer_cannot_supply_the_total_or_policy_fields(client, customer, incident):
    assert client.post("/my/claims", json={**incident, "total_claim_amount": 1}, headers=customer).status_code == 422
    assert client.post("/my/claims", json={**incident, "policy_csl": "500/1000"}, headers=customer).status_code == 422


def test_customers_only_see_their_own_claims(client, admin, customer, profile, incident):
    _create(client, admin, "other", "customer", profile)
    other = _headers(client, "other", "a long password 1")
    client.post("/my/claims", json=incident, headers=customer)
    client.post("/my/claims", json=incident, headers=customer)
    client.post("/my/claims", json=incident, headers=other)
    assert len(client.get("/my/claims", headers=customer).json()) == 2
    assert len(client.get("/my/claims", headers=other).json()) == 1


def test_customer_claim_reaches_the_organization_with_the_full_analysis(client, admin, customer, incident, profile):
    claim_id = client.post("/my/claims", json=incident, headers=customer).json()["id"]
    listed = {c["id"]: c for c in client.get("/claims", headers=admin).json()}
    assert listed[claim_id]["submitted_by"] == "carol"
    detail = client.get(f"/claims/{claim_id}", headers=admin).json()
    assert detail["fraud_result"]["status"] == "ok"
    assert detail["claim"]["policy_csl"] == profile["policy_csl"]  # merged from the profile


@pytest.mark.parametrize("confirmed,expected", [("N", "approved"), ("Y", "not_approved")])
def test_investigator_decision_becomes_the_customer_status(client, investigator, customer, incident, confirmed, expected):
    claim_id = client.post("/my/claims", json=incident, headers=customer).json()["id"]
    fb = {"investigator_decision": "agreed", "confirmed_fraud_reported": confirmed}
    assert client.post(f"/claims/{claim_id}/feedback", json=fb, headers=investigator).status_code == 200
    mine = client.get("/my/claims", headers=customer).json()
    assert mine[0]["status"] == expected and mine[0]["reviewed_at"]
    assert client.get("/claims", headers=investigator).json()[0]["outcome"] == expected


def test_customer_sees_the_policy_on_file(client, customer, profile):
    policy = client.get("/my/policy", headers=customer).json()
    assert policy["auto_model"] == profile["auto_model"] and "age" not in policy


# --- account lifecycle ------------------------------------------------------
def test_deactivation_locks_out_existing_tokens_immediately(client, admin, profile):
    user_id = _create(client, admin, "gone", "customer", profile).json()["id"]
    headers = _headers(client, "gone", "a long password 1")
    assert client.get("/my/claims", headers=headers).status_code == 200
    assert client.post(f"/users/{user_id}/active", json={"active": False}, headers=admin).json()["active"] is False
    assert client.get("/my/claims", headers=headers).status_code == 401
    assert client.post("/auth/login", json={"username": "gone", "password": "a long password 1"}).status_code == 401
    client.post(f"/users/{user_id}/active", json={"active": True}, headers=admin)
    assert client.get("/my/claims", headers=headers).status_code == 200


def test_password_reset_forces_a_change_at_next_sign_in(client, admin, profile):
    user_id = _create(client, admin, "forgetful", "customer", profile).json()["id"]
    assert client.post(f"/users/{user_id}/password", json={"password": "brand new password"},
                       headers=admin).status_code == 204
    assert client.post("/auth/login", json={"username": "forgetful", "password": "a long password 1"}).status_code == 401
    res = client.post("/auth/login", json={"username": "forgetful", "password": "brand new password"})
    assert res.json()["user"]["must_change_password"] is True
    temp = {"Authorization": f"Bearer {res.json()['access_token']}"}
    assert client.get("/my/claims", headers=temp).status_code == 403  # blocked until changed
    client.post("/auth/password", json={"current_password": "brand new password", "new_password": "mine and only mine"},
                headers=temp)
    assert client.get("/my/claims", headers=temp).status_code == 200


def test_investigator_cannot_touch_staff_accounts(client, admin, investigator):
    staff_id = _create(client, admin, "colleague", "investigator").json()["id"]
    assert client.post(f"/users/{staff_id}/active", json={"active": False}, headers=investigator).status_code == 404
    assert client.post(f"/users/{staff_id}/password", json={"password": "hijacked password"},
                       headers=investigator).status_code == 404


def test_unknown_and_deactivated_users_fail_login_with_the_same_error(client, admin, profile):
    user_id = _create(client, admin, "paused", "customer", profile).json()["id"]
    client.post(f"/users/{user_id}/active", json={"active": False}, headers=admin)
    a = client.post("/auth/login", json={"username": "paused", "password": "a long password 1"})
    b = client.post("/auth/login", json={"username": "nobody", "password": "a long password 1"})
    assert a.status_code == b.status_code == 401 and a.json() == b.json()
