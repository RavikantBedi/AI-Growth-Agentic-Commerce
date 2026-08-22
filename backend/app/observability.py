"""Structured local logging and request correlation.

Every request gets a `request_id` that flows into the log line and into every
audit event written during that request, so a money action can be traced from
the HTTP call to the provider call to the audit row.

Secrets never reach the logs: `_SecretFilter` scrubs anything resembling a
Razorpay key, an API key or a bearer token from formatted output.
"""
from __future__ import annotations

import json
import logging
import re
import sys
import time
import uuid

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

_SECRET_PATTERNS = [
    re.compile(r"(rzp_(?:test|live)_[A-Za-z0-9]{6})[A-Za-z0-9]+"),
    re.compile(r"(sk-ant-[A-Za-z0-9]{6})[A-Za-z0-9\-_]+"),
    re.compile(r"(Bearer\s+[A-Za-z0-9]{4})[A-Za-z0-9\.\-_]+", re.IGNORECASE),
    re.compile(r"(Basic\s+[A-Za-z0-9]{4})[A-Za-z0-9+/=]+", re.IGNORECASE),
    re.compile(r"((?:key_secret|api_key|password|secret)[\"']?\s*[:=]\s*[\"']?)[^\s\"',}]+",
               re.IGNORECASE),
]


class _SecretFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            return True
        redacted = message
        for pattern in _SECRET_PATTERNS:
            redacted = pattern.sub(r"\1…<redacted>", redacted)
        if redacted != message:
            record.msg, record.args = redacted, ()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in ("request_id", "session_id", "order_id", "action", "status",
                    "duration_ms"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO", json_logs: bool = False) -> None:
    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        JsonFormatter() if json_logs else
        logging.Formatter("%(asctime)s %(levelname)-7s %(name)-24s %(message)s",
                          datefmt="%H:%M:%S")
    )
    handler.addFilter(_SecretFilter())
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assign a request id, time the request, and log the outcome."""

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        request.state.request_id = request_id
        started = time.perf_counter()
        log = logging.getLogger("http")

        try:
            response = await call_next(request)
        except Exception:
            duration = (time.perf_counter() - started) * 1000
            log.exception("request failed", extra={
                "request_id": request_id, "action": f"{request.method} {request.url.path}",
                "status": "500", "duration_ms": round(duration, 1)})
            raise

        duration = (time.perf_counter() - started) * 1000
        response.headers["x-request-id"] = request_id
        response.headers["x-response-time-ms"] = f"{duration:.1f}"
        level = logging.WARNING if response.status_code >= 400 else logging.INFO
        log.log(level, "%s %s -> %s in %.1fms", request.method, request.url.path,
                response.status_code, duration,
                extra={"request_id": request_id, "status": str(response.status_code),
                       "duration_ms": round(duration, 1)})
        return response


__all__ = ["configure_logging", "RequestContextMiddleware", "JsonFormatter"]
