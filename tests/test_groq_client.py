"""Groq client: guard order, retry policy, and that no secret ever reaches the logs."""

import logging

import httpx
import pytest

import config
from agents import groq_client
from agents.groq_client import LLMUnavailable

SECRET = "gsk-test-secret-value-123"


class FakeResponse:
    def __init__(self, status_code: int = 200, payload=None, headers=None) -> None:
        self.status_code = status_code
        self.headers = headers or {}
        self._payload = payload if payload is not None else {
            "choices": [{"message": {"content": "hello"}}]
        }

    def json(self):
        return self._payload


@pytest.fixture
def with_key(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", SECRET)


@pytest.fixture
def http(monkeypatch):
    """Replace httpx.post; tests queue responses/exceptions and inspect calls."""
    state = {"calls": [], "queue": []}

    def fake_post(url, json=None, headers=None, timeout=None):
        state["calls"].append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        item = state["queue"].pop(0) if state["queue"] else FakeResponse()
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(groq_client.httpx, "post", fake_post)
    return state


def test_no_api_key_makes_no_network_call(http):
    with pytest.raises(LLMUnavailable) as err:
        groq_client.chat("sys", "user")
    assert err.value.reason == "no_api_key" and http["calls"] == []


def test_success_sends_bounded_request_and_returns_text(with_key, http):
    assert groq_client.chat("sys", "user") == "hello"
    call = http["calls"][0]
    assert call["url"] == config.GROQ_API_URL
    assert call["headers"]["Authorization"] == f"Bearer {SECRET}"
    assert call["json"]["max_completion_tokens"] == config.GROQ_MAX_OUTPUT_TOKENS
    assert call["timeout"] == config.GROQ_TIMEOUT_S


def test_request_targets_groq_and_uses_json_mode(with_key, http):
    groq_client.chat("sys", "user")
    call = http["calls"][0]
    assert call["url"] == "https://api.groq.com/openai/v1/chat/completions"
    assert call["json"]["response_format"] == {"type": "json_object"}
    assert call["json"]["model"] == config.GROQ_MODEL


def test_reasoning_effort_is_sent_when_configured_and_omitted_when_blank(with_key, http, monkeypatch):
    monkeypatch.setattr(config, "GROQ_REASONING_EFFORT", "low")
    groq_client.chat("s", "u")
    assert http["calls"][0]["json"]["reasoning_effort"] == "low"
    monkeypatch.setattr(config, "GROQ_REASONING_EFFORT", "")  # non-reasoning model: Groq would 400
    groq_client.chat("s", "u")
    assert "reasoning_effort" not in http["calls"][1]["json"]


def test_short_retry_after_is_honoured_on_429(with_key, http, monkeypatch):
    slept = []
    monkeypatch.setattr(groq_client.time, "sleep", slept.append)
    http["queue"] = [FakeResponse(429, headers={"retry-after": "2"}), FakeResponse(200)]
    assert groq_client.chat("s", "u") == "hello"
    assert slept == [2.0] and len(http["calls"]) == 2


def test_long_retry_after_falls_back_immediately_without_retrying(with_key, http, monkeypatch):
    slept = []
    monkeypatch.setattr(groq_client.time, "sleep", slept.append)
    http["queue"] = [FakeResponse(429, headers={"retry-after": "45"}), FakeResponse(200)]
    with pytest.raises(LLMUnavailable) as err:
        groq_client.chat("s", "u")
    assert err.value.reason == "rate_limited"
    assert len(http["calls"]) == 1 and slept == []  # never stalls the request for 45 s


def test_retryable_429_is_retried_once_then_succeeds(with_key, http):
    http["queue"] = [FakeResponse(429), FakeResponse(200)]
    assert groq_client.chat("s", "u") == "hello"
    assert len(http["calls"]) == 2


def test_retries_are_capped(with_key, http):
    http["queue"] = [FakeResponse(503)] * 5
    with pytest.raises(LLMUnavailable) as err:
        groq_client.chat("s", "u")
    assert err.value.reason == "http_503"
    assert len(http["calls"]) == config.GROQ_MAX_RETRIES + 1  # never an unbounded loop


def test_auth_error_is_not_retried(with_key, http):
    http["queue"] = [FakeResponse(401)]
    with pytest.raises(LLMUnavailable) as err:
        groq_client.chat("s", "u")
    assert err.value.reason == "http_401" and len(http["calls"]) == 1


def test_timeout_maps_to_llm_unavailable(with_key, http):
    http["queue"] = [httpx.ReadTimeout("slow"), httpx.ReadTimeout("slow")]
    with pytest.raises(LLMUnavailable) as err:
        groq_client.chat("s", "u")
    assert err.value.reason == "timeout"


def test_per_minute_budget_stops_further_http_calls(with_key, http, monkeypatch):
    monkeypatch.setattr(config, "GROQ_MAX_CALLS_PER_MINUTE", 2)
    groq_client.reset_guards()
    groq_client.chat("s", "u")
    groq_client.chat("s", "u")
    with pytest.raises(LLMUnavailable) as err:
        groq_client.chat("s", "u")
    assert err.value.reason == "budget_exhausted"
    assert len(http["calls"]) == 2  # the refused call never reached the network


def test_circuit_opens_after_consecutive_failures_and_stops_calling(with_key, http, monkeypatch):
    monkeypatch.setattr(config, "GROQ_BREAKER_FAILURES", 2)
    monkeypatch.setattr(config, "GROQ_MAX_RETRIES", 0)
    groq_client.reset_guards()
    http["queue"] = [FakeResponse(500), FakeResponse(500)]
    for _ in range(2):
        with pytest.raises(LLMUnavailable):
            groq_client.chat("s", "u")
    with pytest.raises(LLMUnavailable) as err:
        groq_client.chat("s", "u")
    assert err.value.reason == "circuit_open" and len(http["calls"]) == 2


def test_malformed_response_is_rejected(with_key, http):
    http["queue"] = [FakeResponse(200, {"unexpected": True})]
    with pytest.raises(LLMUnavailable) as err:
        groq_client.chat("s", "u")
    assert err.value.reason == "malformed_response"


def test_api_key_never_appears_in_logs(with_key, http, caplog):
    http["queue"] = [FakeResponse(500), FakeResponse(401)]
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(LLMUnavailable):
            groq_client.chat("sys", "user")
    assert SECRET not in caplog.text


def test_status_endpoint_data_contains_no_secret(with_key):
    assert SECRET not in str(groq_client.status())
    assert groq_client.status()["configured"] is True
