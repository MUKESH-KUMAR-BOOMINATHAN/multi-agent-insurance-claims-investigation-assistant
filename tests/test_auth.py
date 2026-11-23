"""Authentication: password hashing, tokens, startup validation and the protected API."""

import jwt
import pytest
from fastapi.testclient import TestClient

import auth
import config
import main
from resilience import KeyedRateLimiter

PASSWORD = "correct horse battery staple"
SECRET = "s" * 48


@pytest.fixture(scope="module")
def password_hash():
    return auth.hash_password(PASSWORD)  # scrypt is deliberately slow: do it once


@pytest.fixture
def auth_on(monkeypatch, password_hash):
    """Turn authentication on with a known operator account."""
    monkeypatch.setattr(config, "AUTH_DISABLED", False)
    monkeypatch.setattr(config, "ADMIN_USERNAME", "operator")
    monkeypatch.setattr(config, "ADMIN_PASSWORD_HASH", password_hash)
    monkeypatch.setattr(config, "JWT_SECRET", SECRET)
    monkeypatch.setattr(main, "_login_limiter", KeyedRateLimiter(1_000))
    monkeypatch.setattr(main, "_claims_limiter", KeyedRateLimiter(1_000))


@pytest.fixture
def client(auth_on):
    return TestClient(main.app)


def _login(client, username="operator", password=PASSWORD):
    return client.post("/auth/login", json={"username": username, "password": password})


# --- passwords --------------------------------------------------------------
def test_hash_verifies_the_right_password_only(password_hash):
    assert auth.verify_password(PASSWORD, password_hash)
    assert not auth.verify_password(PASSWORD + "x", password_hash)
    assert not auth.verify_password("", password_hash)


def test_hashes_are_salted_and_never_contain_the_password(password_hash):
    assert auth.hash_password(PASSWORD) != password_hash
    assert PASSWORD not in password_hash and password_hash.startswith("scrypt$")


@pytest.mark.parametrize("stored", ["", "plaintext", "scrypt$1$2", "bcrypt$a$b$c$d$e", "scrypt$x$y$z$zz$zz"])
def test_malformed_stored_hashes_fail_closed_without_raising(stored):
    assert auth.verify_password(PASSWORD, stored) is False


def test_authenticate_requires_both_username_and_password(auth_on):
    assert auth.authenticate("operator", PASSWORD)
    assert not auth.authenticate("operator", "wrong")
    assert not auth.authenticate("someone-else", PASSWORD)
    assert not auth.authenticate("", "")


def test_authenticate_fails_when_no_account_is_configured(monkeypatch):
    monkeypatch.setattr(config, "ADMIN_USERNAME", "")
    monkeypatch.setattr(config, "ADMIN_PASSWORD_HASH", "")
    assert auth.authenticate("", "") is False


# --- tokens -----------------------------------------------------------------
def test_token_round_trip(auth_on):
    assert auth.decode_token(auth.create_token("operator")) == "operator"


def test_expired_token_is_rejected(auth_on, monkeypatch):
    monkeypatch.setattr(config, "TOKEN_TTL_MINUTES", -5)
    with pytest.raises(jwt.ExpiredSignatureError):
        auth.decode_token(auth.create_token("operator"))


def test_tampered_or_foreign_tokens_are_rejected(auth_on):
    good = auth.create_token("operator")
    with pytest.raises(jwt.PyJWTError):
        auth.decode_token(good[:-3] + ("AAA" if not good.endswith("AAA") else "BBB"))
    with pytest.raises(jwt.PyJWTError):  # signed with a different secret
        auth.decode_token(jwt.encode({"sub": "x", "iss": auth.ISSUER, "iat": 1, "exp": 9_999_999_999}, "z" * 48, "HS256"))
    with pytest.raises(jwt.PyJWTError):  # wrong issuer
        auth.decode_token(jwt.encode({"sub": "x", "iss": "evil", "iat": 1, "exp": 9_999_999_999}, SECRET, "HS256"))


def test_unsigned_alg_none_token_is_rejected(auth_on):
    forged = jwt.encode({"sub": "operator", "iss": auth.ISSUER, "iat": 1, "exp": 9_999_999_999}, key=None, algorithm="none")
    with pytest.raises(jwt.PyJWTError):
        auth.decode_token(forged)


# --- startup validation ------------------------------------------------------
def test_disabled_auth_is_refused_in_production(monkeypatch):
    monkeypatch.setattr(config, "AUTH_DISABLED", True)
    monkeypatch.setattr(config, "IS_PRODUCTION", True)
    with pytest.raises(RuntimeError, match="AUTH_DISABLED"):
        auth.validate_auth_config()


def test_disabled_auth_is_allowed_in_development(monkeypatch):
    monkeypatch.setattr(config, "AUTH_DISABLED", True)
    monkeypatch.setattr(config, "IS_PRODUCTION", False)
    auth.validate_auth_config()  # does not raise


@pytest.mark.parametrize("missing", ["ADMIN_USERNAME", "ADMIN_PASSWORD_HASH", "JWT_SECRET"])
def test_enabled_auth_without_full_configuration_refuses_to_start(auth_on, monkeypatch, missing):
    monkeypatch.setattr(config, missing, "")
    with pytest.raises(RuntimeError, match=missing):
        auth.validate_auth_config()


def test_short_jwt_secret_and_non_scrypt_hash_are_refused(auth_on, monkeypatch):
    monkeypatch.setattr(config, "JWT_SECRET", "short")
    with pytest.raises(RuntimeError, match="at least"):
        auth.validate_auth_config()
    monkeypatch.setattr(config, "JWT_SECRET", SECRET)
    monkeypatch.setattr(config, "ADMIN_PASSWORD_HASH", "hunter2")
    with pytest.raises(RuntimeError, match="scrypt"):
        auth.validate_auth_config()


def test_a_fully_configured_account_passes_validation(auth_on):
    auth.validate_auth_config()


# --- the protected API --------------------------------------------------------
def test_claims_routes_require_a_token(client, valid_claim):
    for method, path, kwargs in (
        ("get", "/claims", {}),
        ("get", "/claims/1", {}),
        ("post", "/claims", {"json": valid_claim}),
        ("post", "/claims/1/feedback", {"json": {"investigator_decision": "agreed", "confirmed_fraud_reported": "N"}}),
    ):
        response = getattr(client, method)(path, **kwargs)
        assert response.status_code == 401, path
        assert response.headers["www-authenticate"] == "Bearer"


def test_garbage_and_non_bearer_credentials_are_rejected(client):
    assert client.get("/claims", headers={"Authorization": "Bearer not-a-jwt"}).status_code == 401
    assert client.get("/claims", headers={"Authorization": "Basic b3BlcmF0b3I6cHc="}).status_code == 401
    assert client.get("/claims", headers={"Authorization": "Bearer "}).status_code == 401


def test_login_issues_a_token_that_unlocks_the_api(client, valid_claim):
    body = _login(client).json()
    assert body["token_type"] == "bearer" and body["expires_in"] == config.TOKEN_TTL_MINUTES * 60
    headers = {"Authorization": f"Bearer {body['access_token']}"}
    assert client.get("/claims", headers=headers).status_code == 200
    assert client.post("/claims", json=valid_claim, headers=headers).status_code == 200


def test_wrong_password_and_unknown_user_get_the_same_generic_error(client):
    wrong_password = _login(client, password="nope")
    unknown_user = _login(client, username="ghost")
    assert wrong_password.status_code == unknown_user.status_code == 401
    assert wrong_password.json() == unknown_user.json() == {"detail": "Invalid credentials"}


def test_login_is_rate_limited_per_client(client, monkeypatch):
    monkeypatch.setattr(main, "_login_limiter", KeyedRateLimiter(3))
    assert [_login(client, password="bad").status_code for _ in range(3)] == [401, 401, 401]
    blocked = _login(client, password="bad")
    assert blocked.status_code == 429 and int(blocked.headers["retry-after"]) >= 1
    assert _login(client).status_code == 429  # even the right password waits: no free guesses


def test_failed_login_log_never_contains_the_attempted_credentials(client, caplog):
    import logging

    with caplog.at_level(logging.WARNING):
        _login(client, username="operator-typo", password="my-secret-guess")
    assert "my-secret-guess" not in caplog.text and "operator-typo" not in caplog.text


def test_health_and_auth_config_are_public(client):
    assert client.get("/health").status_code == 200
    assert client.get("/auth/config").json() == {"auth_required": True}


def test_auth_config_reports_disabled_in_development(monkeypatch):
    monkeypatch.setattr(config, "AUTH_DISABLED", True)
    monkeypatch.setattr(config, "IS_PRODUCTION", False)
    assert TestClient(main.app).get("/auth/config").json() == {"auth_required": False}


def test_auth_cannot_be_bypassed_by_the_disable_flag_in_production(auth_on, monkeypatch):
    monkeypatch.setattr(config, "AUTH_DISABLED", True)
    monkeypatch.setattr(config, "IS_PRODUCTION", True)
    client = TestClient(main.app)
    assert client.get("/claims").status_code == 401
    assert client.get("/auth/config").json() == {"auth_required": True}
