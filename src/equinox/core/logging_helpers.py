"""Helpers to DRY logging of requests/responses/errors."""

from __future__ import annotations

from typing import Dict, Any

from equinox.core.logging_payload import request_payload, response_payload, error_payload
from equinox.core.security_policy import redact_headers, redact_url, redact_body


def log_request_with_payload(logger, payload: Dict[str, Any]) -> None:
    # Expect a dict payload prepared by request_payload or similar
    logger.log_request(payload)  # type: ignore[arg-type]


def log_response_with_payload(logger, payload: Dict[str, Any]) -> None:
    logger.log_response(payload)  # type: ignore[arg-type]


def log_error_with_payload(logger, payload: Dict[str, Any]) -> None:
    logger.log_error(payload)  # type: ignore[arg-type]
