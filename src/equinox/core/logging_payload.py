"""Centralized logging payload builders for DRY surface."""

from __future__ import annotations

from typing import Any, Dict, Optional

from equinox.core.redact import redact_headers, redact_url, redact_body
from equinox.core.request.request import Request
from equinox.core.request.response import Response


def _safe_body_preview(body: Optional[Any], limit: int = 1000) -> str:
    if body is None:
        return ""
    if isinstance(body, (bytes, bytearray)):
        return body[:limit].decode(errors="replace")
    s = body if isinstance(body, str) else str(body)
    return s[:limit]


def request_payload(request: Request, include_body: bool = False) -> Dict[str, Any]:
    payload = {
        "method": request.method,
        "url": redact_url(request.url),
        "headers": redact_headers(request.headers or {}),
        "params": dict(request.params or {}),
        "timeout": request.timeout,
        "verify_ssl": request.verify_ssl,
    }
    if include_body:
        payload["body"] = redact_body(_safe_body_preview(request.body), max_length=1000)
    return payload


def response_payload(request: Request, response: Optional[Response], elapsed_time: float, include_body: bool = False) -> Dict[str, Any]:
    payload = {
        "method": request.method,
        "url": redact_url(request.url),
        "status_code": None if response is None else response.status_code,
        "reason": None if response is None else response.reason,
        "elapsed_time_seconds": elapsed_time,
        "headers": redact_headers(dict(response.headers) if response and response.headers else {}),
    }
    if include_body and response is not None:
        payload["body"] = redact_body(_safe_body_preview(response.body), max_length=1000)
    return payload


def error_payload(request: Request, error: Exception) -> Dict[str, Any]:
    return {
        "method": request.method,
        "url": redact_url(request.url),
        "error_type": type(error).__name__,
        "error_message": redact_body(str(error), max_len=500),
    }
