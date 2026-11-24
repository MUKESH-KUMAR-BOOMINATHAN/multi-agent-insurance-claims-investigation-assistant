"""Account lockout, self-service password change, temporary passwords and the audit log."""

import json

import pytest
from fastapi.testclient import TestClient

import auth
import config
import main
from db import security_store
from resilience import KeyedRateLimiter

ADMIN_PW = "admin password 456"
TEMP = "temporary pass 9"
FINAL = "my own password 9"


@pytest.fixture(scope="module")
def admin_hash():
    return auth.hash_password(ADMIN_PW)


@pytest.fixture
def client(monkeypatch, admin_hash):
    monkeypatch.setattr(config, "AUTH_DISABLED", False)
    monkeypatch.setattr(config, "ADMIN_USERNAME", "boss")
    monkeypatch.setattr(config, "ADMIN_PASSWORD_HASH", admin_hash)
    monkeypatch.setattr(config, "JWT_SECRET", "k" * 48)
    monkeypatch.setattr(config, "LOCKOUT_AFTER_FAILURES", 3)
    monkeypatch.setattr(main, "_login_limiter", KeyedRateLimiter(1_000))  # isolate the per-account lock
    monkeypatch.setattr(main, "_claims_limiter", KeyedRateLimiter(1_000))
    return TestClient(main.app)


def _login(client, username, password):
    return client.post("/auth/login", json={"username": username, "password": password})


def _bearer(res):
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


@pytest.fixture
def admin(client):
    return _bearer(_login(client, "boss", ADMIN_PW))


@pytest.fixture
def staff(client, admin):
    """An investigator account that still has its temporary password."""
    res = client.post("/users", json={"username": "sam", "full_name": "Sam", "password": TEMP,
                                      "role": "investigator"}, headers=admin)
    assert res.status_code == 201 and res.json()["must_change_password"] is True
    return res.json()


# --- lockout ----------------------------------------------------------------
def test_account_locks_after_repeated_failures_even_for_the_right_password(client, staff):
    for _ in range(3):
        assert _login(client, "sam", "wrong password").status_code == 401
    locked = _login(client, "sam", TEMP)
    assert locked.status_code == 429 and "Retry-After" in locked.headers


def test_unknown_usernames_lock_the_same_way_so_existence_is_not_revealed(client):
    for _ in range(3):
        _login(client, "ghost", "whatever")
    assert _login(client, "ghost", "whatever").status_code == 429


def test_a_successful_sign_in_resets_the_failure_count(client, staff):
    _login(client, "sam", "wrong")
    _login(client, "sam", "wrong")
    assert _login(client, "sam", TEMP).status_code == 200
    _login(client, "sam", "wrong")
    _login(client, "sam", "wrong")
    assert _login(client, "sam", TEMP).status_code == 200  # only 2 in a row since the success


def test_lockout_is_stored_in_the_database_not_in_memory(client, staff, monkeypatch):
    for _ in range(3):
        _login(client, "sam", "wrong")
    monkeypatch.setattr(main, "_login_limiter", KeyedRateLimiter(1_000))  # "restart": memory is fresh
    assert security_store.seconds_locked("sam") is not None
    assert _login(client, "sam", TEMP).status_code == 429


def test_lock_is_per_account_case_insensitively(client, staff):
    for _ in range(3):
        _login(client, "SAM", "wrong")
    assert _login(client, "sam", TEMP).status_code == 429


def test_a_password_reset_by_the_organization_also_unlocks(client, admin, staff):
    for _ in range(3):
        _login(client, "sam", "wrong")
    client.post(f"/users/{staff['id']}/password", json={"password": "reset by admin 1"}, headers=admin)
    assert _login(client, "sam", "reset by admin 1").status_code == 200


# --- temporary passwords and self-service change ---------------------------
def test_a_temporary_password_only_allows_changing_it(client, staff):
    temp = _bearer(_login(client, "sam", TEMP))
    assert client.get("/auth/me", headers=temp).json()["must_change_password"] is True
    assert client.get("/claims", headers=temp).status_code == 403
    assert client.get("/users", headers=temp).status_code == 403
    assert client.post("/auth/password", json={"current_password": TEMP, "new_password": FINAL},
                       headers=temp).status_code == 204
    assert client.get("/claims", headers=temp).status_code == 200  # same token, now allowed
    assert client.get("/auth/me", headers=temp).json()["must_change_password"] is False


@pytest.mark.parametrize("current,new,status", [
    ("not my password", FINAL, 400),   # wrong current password
    (TEMP, TEMP, 422),                 # must actually change
    (TEMP, "short", 422),              # too short
])
def test_password_change_rules(client, staff, current, new, status):
    temp = _bearer(_login(client, "sam", TEMP))
    assert client.post("/auth/password", json={"current_password": current, "new_password": new},
                       headers=temp).status_code == status


def test_the_old_password_stops_working_after_a_change(client, staff):
    temp = _bearer(_login(client, "sam", TEMP))
    client.post("/auth/password", json={"current_password": TEMP, "new_password": FINAL}, headers=temp)
    assert _login(client, "sam", TEMP).status_code == 401
    assert _login(client, "sam", FINAL).json()["user"]["must_change_password"] is False


def test_the_admin_password_is_managed_on_the_server_only(client, admin):
    res = client.post("/auth/password", json={"current_password": ADMIN_PW, "new_password": "something else 1"},
                      headers=admin)
    assert res.status_code == 400


# --- audit log --------------------------------------------------------------
def test_admin_sees_activity_and_it_never_contains_secrets(client, admin, staff):
    _login(client, "sam", "a wrong guess")
    temp = _bearer(_login(client, "sam", TEMP))
    client.post("/auth/password", json={"current_password": TEMP, "new_password": FINAL}, headers=temp)
    client.post(f"/users/{staff['id']}/active", json={"active": False}, headers=admin)

    events = client.get("/audit", headers=admin).json()
    actions = [e["action"] for e in events]
    for expected in ("login", "account_created", "login_failed", "password_changed", "account_deactivated"):
        assert expected in actions
    dumped = json.dumps(events)
    for secret in (ADMIN_PW, TEMP, FINAL, "a wrong guess"):
        assert secret not in dumped


def test_failed_sign_ins_for_unknown_names_are_not_logged(client, admin):
    _login(client, "Hunter2-typed-in-the-wrong-box", "x")
    assert "Hunter2" not in json.dumps(client.get("/audit", headers=admin).json())


def test_only_the_admin_can_read_the_audit_log(client, staff):
    temp = _bearer(_login(client, "sam", TEMP))
    client.post("/auth/password", json={"current_password": TEMP, "new_password": FINAL}, headers=temp)
    assert client.get("/audit", headers=temp).status_code == 403


# --- production storage guard ---------------------------------------------
def test_production_refuses_to_start_without_a_durable_database(monkeypatch, admin_hash):
    monkeypatch.setattr(config, "AUTH_DISABLED", False)
    monkeypatch.setattr(config, "IS_PRODUCTION", True)
    monkeypatch.setattr(config, "ADMIN_USERNAME", "boss")
    monkeypatch.setattr(config, "ADMIN_PASSWORD_HASH", admin_hash)
    monkeypatch.setattr(config, "JWT_SECRET", "k" * 48)
    monkeypatch.setattr(config, "CORS_ORIGINS", ["https://example.vercel.app"])
    monkeypatch.setattr(config, "DATABASE_URL", None)
    monkeypatch.delenv("DATA_DIR", raising=False)
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        main._validate_runtime_config()
    monkeypatch.setenv("DATA_DIR", "/var/data")  # a persistent disk is an explicit, valid choice
    main._validate_runtime_config()
    monkeypatch.delenv("DATA_DIR")
    monkeypatch.setattr(config, "DATABASE_URL", "postgresql://u:p@db.example/neondb")
    main._validate_runtime_config()


def test_health_reports_the_backend_without_touching_the_database(monkeypatch):
    from db import engine as db_engine

    def boom(*_a, **_k):
        raise AssertionError("health must not open a database connection")

    monkeypatch.setattr(db_engine, "get_engine", boom)
    body = TestClient(main.app).get("/health").json()
    assert body["database"] in ("sqlite", "postgres")
