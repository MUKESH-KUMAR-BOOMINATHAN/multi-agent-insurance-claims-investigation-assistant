"""
groq_client.py

Thin, guarded client for the Groq chat-completions API
(``POST https://api.groq.com/openai/v1/chat/completions``, Bearer auth; the
API is OpenAI-compatible). Requests use JSON mode and ``max_completion_tokens``.

Every call is protected so a misbehaving API can neither hang a request nor
run up a bill:

    1. no API key configured         -> LLMUnavailable("no_api_key")      (no network)
    2. circuit breaker open          -> LLMUnavailable("circuit_open")    (no network)
    3. per-minute / per-day budget   -> LLMUnavailable("budget_exhausted") (no network)
    4. HTTP call with a hard timeout and ``max_completion_tokens`` cap
    5. at most GROQ_MAX_RETRIES retries on 429/5xx/timeout, each re-checked
       against the budget. On 429 the ``retry-after`` header is honoured, but
       only if short (GROQ_MAX_RETRY_WAIT_S); otherwise fall back immediately
       instead of stalling the request.

Callers catch :class:`LLMUnavailable` and fall back to the rules-based path.
The API key and the prompt/response text are never logged.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

import config
from resilience import CircuitBreaker, SlidingWindowBudget

log = logging.getLogger(__name__)

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
_BACKOFF_S = 1.0


class LLMUnavailable(Exception):
    """The LLM could not be used; ``reason`` is a short machine-readable code."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


_budget = SlidingWindowBudget(config.GROQ_MAX_CALLS_PER_MINUTE, config.GROQ_MAX_CALLS_PER_DAY)
_breaker = CircuitBreaker(config.GROQ_BREAKER_FAILURES, config.GROQ_BREAKER_COOLDOWN_S)


def reset_guards() -> None:
    """Recreate budget + breaker from current config (used by tests)."""
    global _budget, _breaker
    _budget = SlidingWindowBudget(config.GROQ_MAX_CALLS_PER_MINUTE, config.GROQ_MAX_CALLS_PER_DAY)
    _breaker = CircuitBreaker(config.GROQ_BREAKER_FAILURES, config.GROQ_BREAKER_COOLDOWN_S)


def status() -> dict[str, Any]:
    """Non-secret operational status for the /health endpoint."""
    return {
        "configured": config.get_groq_api_key() is not None,
        "model": config.GROQ_MODEL,
        "circuit": _breaker.state,
        "budget": _budget.snapshot(),
    }


def _retry_after_seconds(response: Any) -> float | None:
    """Seconds from a ``retry-after`` header, or None if absent/unparseable."""
    raw = getattr(response, "headers", {}).get("retry-after")
    try:
        return max(0.0, float(raw)) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _extract_text(payload: dict[str, Any]) -> str:
    """Pull ``choices[0].message.content`` out of a chat-completions response."""
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMUnavailable("malformed_response") from exc
    if not isinstance(content, str) or not content.strip():
        raise LLMUnavailable("empty_response")
    return content


def chat(system_prompt: str, user_prompt: str) -> str:
    """Send one guarded chat request and return the assistant's text.

    Raises:
        LLMUnavailable: for any condition under which the caller should use
            its fallback (see module docstring for the reason codes).
    """
    api_key = config.get_groq_api_key()
    if api_key is None:
        raise LLMUnavailable("no_api_key")

    body = {
        "model": config.GROQ_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.1,  # judgment task, not creative writing
        "max_completion_tokens": config.GROQ_MAX_OUTPUT_TOKENS,
        # JSON mode: the model must emit a valid JSON object (our prompt asks for one).
        # The reply is still schema-validated by the caller.
        "response_format": {"type": "json_object"},
        "stream": False,
    }
    if config.GROQ_REASONING_EFFORT:
        body["reasoning_effort"] = config.GROQ_REASONING_EFFORT
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    last_reason = "unknown_error"
    for attempt in range(config.GROQ_MAX_RETRIES + 1):
        retry_wait: float | None = None  # set from `retry-after` on a short 429
        if not _breaker.allow():
            raise LLMUnavailable("circuit_open")
        if not _budget.try_acquire():
            log.warning("Groq call budget exhausted; using fallback")
            raise LLMUnavailable("budget_exhausted")

        try:
            response = httpx.post(
                config.GROQ_API_URL, json=body, headers=headers, timeout=config.GROQ_TIMEOUT_S
            )
        except httpx.TimeoutException:
            last_reason = "timeout"
        except httpx.HTTPError as exc:
            # Log the exception class only: the message can embed the URL/headers.
            log.warning("Groq transport error: %s", type(exc).__name__)
            last_reason = "network_error"
        else:
            if response.status_code == 200:
                try:
                    text = _extract_text(response.json())
                except (ValueError, LLMUnavailable) as exc:
                    _breaker.record_failure()
                    raise LLMUnavailable(getattr(exc, "reason", "malformed_response")) from exc
                _breaker.record_success()
                return text
            last_reason = f"http_{response.status_code}"
            if response.status_code not in _RETRYABLE_STATUS:
                # Auth/validation errors won't fix themselves: don't retry, don't hammer.
                _breaker.record_failure()
                log.error("Groq returned non-retryable HTTP %s", response.status_code)
                raise LLMUnavailable(last_reason)
            if response.status_code == 429:
                retry_wait = _retry_after_seconds(response)
                if retry_wait is not None and retry_wait > config.GROQ_MAX_RETRY_WAIT_S:
                    _breaker.record_failure()
                    log.warning("Groq rate limited (retry-after %.0fs); using fallback", retry_wait)
                    raise LLMUnavailable("rate_limited")

        _breaker.record_failure()
        log.warning("Groq attempt %d failed (%s)", attempt + 1, last_reason)
        if attempt < config.GROQ_MAX_RETRIES:
            time.sleep(retry_wait if retry_wait is not None else _BACKOFF_S * (2**attempt))

    raise LLMUnavailable(last_reason)
