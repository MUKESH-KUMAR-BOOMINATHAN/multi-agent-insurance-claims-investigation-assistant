"""
middleware.py

Request-level protections for the API:

  BodyLimitMiddleware  rejects oversized request bodies with 413 (checked on the
                       declared Content-Length AND while streaming, so a chunked
                       upload cannot sneak past the header check)
  install_observability  request id, one structured access-log line per request
                       (path only: never the query string, body or headers),
                       and security response headers
"""

from __future__ import annotations

import logging
import re
import time
import uuid

from fastapi import FastAPI, HTTPException
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

import config
from logging_setup import request_id_var

access_log = logging.getLogger("access")
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{8,64}$")


class BodyLimitMiddleware:
    """Pure-ASGI middleware enforcing a maximum request body size."""

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        declared = next((v for k, v in scope["headers"] if k == b"content-length"), None)
        if declared is not None and declared.isdigit() and int(declared) > self.max_bytes:
            await JSONResponse({"detail": "Request body too large"}, status_code=413)(scope, receive, send)
            return

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    # An HTTPException is rendered as a clean 413 by Starlette/FastAPI.
                    raise HTTPException(status_code=413, detail="Request body too large")
            return message

        await self.app(scope, limited_receive, send)


def install_observability(app: FastAPI) -> None:
    """Attach the request-id / access-log / security-header middleware."""

    @app.middleware("http")
    async def observability(request, call_next):  # noqa: ANN001, ANN202 - starlette signature
        incoming = request.headers.get("x-request-id", "")
        request_id = incoming if _REQUEST_ID_RE.match(incoming) else uuid.uuid4().hex
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
        finally:
            if request.url.path != "/health":  # health checks would drown the log
                access_log.info(
                    "request",
                    extra={
                        "method": request.method,
                        "path": request.url.path,
                        "status": status_code,
                        "duration_ms": round((time.perf_counter() - started) * 1000),
                    },
                )
            request_id_var.reset(token)

        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"  # claim data must not be cached by proxies/browsers
        if config.IS_PRODUCTION:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response
