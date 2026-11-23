"""Request-level protections: body limit, security headers, request ids, safe 500s."""

import pytest
from fastapi.testclient import TestClient

import config
import main
from resilience import KeyedRateLimiter


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main, "_claims_limiter", KeyedRateLimiter(1_000))
    return TestClient(main.app, raise_server_exceptions=False)


def test_oversized_declared_body_is_rejected_with_413(client, valid_claim):
    valid_claim["incident_city"] = "x" * (config.MAX_BODY_BYTES + 10)
    response = client.post("/claims", json=valid_claim)
    assert response.status_code == 413 and response.json() == {"detail": "Request body too large"}


def test_oversized_streamed_body_without_content_length_is_rejected(client):
    def chunks():  # chunked transfer: no Content-Length header for the early check to use
        for _ in range(config.MAX_BODY_BYTES // 1024 + 5):
            yield b"x" * 1024

    response = client.post("/claims", content=chunks(), headers={"Content-Type": "application/json"})
    assert response.status_code == 413


def test_normal_requests_are_not_affected_by_the_limit(client, valid_claim):
    assert client.post("/claims", json=valid_claim).status_code == 200


def test_security_headers_are_set_on_every_response(client):
    for response in (client.get("/health"), client.get("/claims/999"), client.get("/does-not-exist")):
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["x-frame-options"] == "DENY"
        assert response.headers["referrer-policy"] == "no-referrer"
        assert response.headers["cache-control"] == "no-store"


def test_hsts_is_sent_only_in_production(client, monkeypatch):
    assert "strict-transport-security" not in client.get("/health").headers
    monkeypatch.setattr(config, "IS_PRODUCTION", True)
    assert "max-age" in client.get("/health").headers["strict-transport-security"]


def test_a_valid_incoming_request_id_is_echoed_and_a_bad_one_replaced(client):
    assert client.get("/health", headers={"X-Request-ID": "trace-abc-12345"}).headers["x-request-id"] == "trace-abc-12345"
    replaced = client.get("/health", headers={"X-Request-ID": "bad id\r\nX-Evil: 1"}).headers["x-request-id"]
    assert replaced != "bad id" and len(replaced) == 32  # a fresh uuid4 hex; header injection impossible


def test_each_request_without_an_id_gets_a_unique_one(client):
    ids = {client.get("/health").headers["x-request-id"] for _ in range(5)}
    assert len(ids) == 5


def test_unhandled_errors_return_a_generic_500_without_internals(client, caplog):
    @main.app.get("/__test_boom__")
    def boom():
        raise RuntimeError("secret internal detail: db password=hunter2")

    response = client.get("/__test_boom__")
    assert response.status_code == 500
    body = response.json()
    assert body["detail"] == "Internal server error" and "request_id" in body
    assert "hunter2" not in response.text and "Traceback" not in response.text
    assert "hunter2" in caplog.text  # the detail is logged server-side for the operator


def test_cors_allows_only_configured_origins(client):
    allowed = config.CORS_ORIGINS[0]
    ok = client.options(
        "/claims",
        headers={"Origin": allowed, "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization"},
    )
    assert ok.headers.get("access-control-allow-origin") == allowed
    assert "authorization" in ok.headers.get("access-control-allow-headers", "").lower()
    evil = client.options(
        "/claims", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"}
    )
    assert "access-control-allow-origin" not in evil.headers


def test_health_exposes_index_and_llm_status_without_secrets(client, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_super_secret_value_1234567890")
    body = client.get("/health").json()
    assert set(body) >= {"status", "fraud_model_loaded", "retrieval", "llm"}
    assert body["retrieval"] == {"ready": False, "documents": None}
    assert "gsk_super_secret_value_1234567890" not in client.get("/health").text
