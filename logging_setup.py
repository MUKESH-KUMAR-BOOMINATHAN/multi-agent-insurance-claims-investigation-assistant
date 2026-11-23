"""
logging_setup.py

Structured logging. In production every log line is one JSON object (so Render's
log stream and any aggregator can filter on fields); locally it is plain text.

Every record carries the current request id (set by the API middleware) so a
single request can be followed across modules. Log *about* the work, never the
work: callers must not pass secrets, tokens or claim contents.
"""

from __future__ import annotations

import contextvars
import json
import logging
import sys
from datetime import UTC, datetime

# Set per request by main.py's middleware; "-" outside a request.
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")

# color_message is uvicorn's ANSI-coloured duplicate of the message: noise in JSON logs.
_RESERVED = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime", "color_message"}


class RequestIdFilter(logging.Filter):
    """Attach the active request id to every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line; exceptions are included as a single string field."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }
        # Structured extras passed via logger.info(..., extra={"duration_ms": 12}).
        for key, value in record.__dict__.items():
            if key not in _RESERVED and key != "request_id" and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def setup_logging(level: str = "INFO", fmt: str = "text") -> None:
    """Configure the root logger once (idempotent)."""
    root = logging.getLogger()
    if getattr(root, "_claimguard_configured", False):
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(RequestIdFilter())
    if fmt == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s [%(request_id)s] %(name)s: %(message)s")
        )
    root.handlers[:] = [handler]
    root.setLevel(level)
    root._claimguard_configured = True  # type: ignore[attr-defined]
    # uvicorn installs its own handlers; route them through ours for one consistent stream.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True
    # middleware.py writes one structured access line per request (path only). uvicorn's
    # own access line would duplicate it AND include the query string, so silence it.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
