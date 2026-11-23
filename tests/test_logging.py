"""Structured logging: valid JSON, request ids, no noise, no duplicated access lines."""

import json
import logging
import sys

from logging_setup import JsonFormatter, request_id_var, setup_logging


def _record(message="request", exc_info=None, **extra):
    record = logging.LogRecord("access", logging.INFO, __file__, 1, message, (), exc_info)
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_json_formatter_emits_one_valid_object_with_request_id_and_extras():
    line = JsonFormatter().format(_record(request_id="abc12345", duration_ms=12))
    parsed = json.loads(line)
    assert "\n" not in line
    assert parsed["message"] == "request" and parsed["request_id"] == "abc12345" and parsed["duration_ms"] == 12


def test_json_formatter_includes_exceptions_as_a_string_field():
    try:
        raise ValueError("boom")
    except ValueError:
        record = _record("failed", exc_info=sys.exc_info())
    assert "ValueError: boom" in json.loads(JsonFormatter().format(record))["exception"]


def test_uvicorns_ansi_colour_duplicate_is_dropped_from_json_logs():
    parsed = json.loads(JsonFormatter().format(_record(color_message="\x1b[36mhello\x1b[0m")))
    assert "color_message" not in parsed


def test_request_id_defaults_to_dash_outside_a_request():
    assert request_id_var.get() == "-"


def test_log_injection_is_impossible_because_values_are_json_escaped():
    forged = 'ok\n{"level": "CRITICAL", "message": "forged"}'
    line = JsonFormatter().format(_record(forged))
    assert "\n" not in line and json.loads(line)["message"] == forged  # one line; the forged text stays data


def test_uvicorns_duplicate_access_log_is_silenced():
    root = logging.getLogger()
    previous = getattr(root, "_claimguard_configured", False)
    root._claimguard_configured = False  # force a fresh configuration for this test
    try:
        setup_logging("INFO", "json")
        assert logging.getLogger("uvicorn.access").level == logging.WARNING
    finally:
        root._claimguard_configured = previous
