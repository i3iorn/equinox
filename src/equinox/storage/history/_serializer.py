"""Serialization of Request/Response objects into database-storable primitives."""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from equinox.core.request import Request, Response
from equinox.core.exceptions import SecurityError, ValidationError
from equinox.core.security_policy import redact_headers, redact_url
from equinox.core.history_config import should_capture_bodies
from equinox.core.serialization import serialize_headers, serialize_body
from equinox.core.constants import MAX_HEADERS_SIZE, MAX_URL_LENGTH, MAX_BODY_SIZE as _MAX_BODY, MAX_ERROR_MESSAGE_LENGTH as _MAX_ERROR_MESSAGE_LENGTH
from equinox.storage.utils import coerce_body_to_str, safe_json_dumps, safe_json_loads

__all__ = ["_HistorySerializer"]

logger = logging.getLogger(__name__)


class _HistorySerializer:
    """Convert Request/Response objects into database-storable scalar primitives.

    All size-limiting thresholds live here as the single source of truth for
    what is stored in the history table.
    """

    MAX_BODY_SIZE            = _MAX_BODY           # 10 MB
    MAX_HEADERS_SIZE         = MAX_HEADERS_SIZE    # 100 KB
    MAX_URL_LENGTH           = MAX_URL_LENGTH      # 2048
    MAX_ERROR_MESSAGE_LENGTH = _MAX_ERROR_MESSAGE_LENGTH

    # ── Public interface ──────────────────────────────────────────────────────

    def prepare_request(self, request: Request) -> Dict[str, Any]:
        """Validate and serialize the request side of a history row.

        Returns:
            Dict with keys: ``method``, ``url``, ``headers_json``, ``body``.

        Raises:
            ValidationError: If *url* or *headers* are invalid.
        """
        body_val = request.body if should_capture_bodies() else None
        return {
            "method":       self._validate_method(request.method),
            "url":          self._prepare_url(request.url),
            "headers_json": self._prepare_headers(request.headers or {}),
            "body":         self._prepare_body(body_val),
        }

    def prepare_response(self, response: Optional[Response]) -> Dict[str, Any]:
        """Serialize the response side of a history row.

        Returns:
            Dict with keys: ``status_code``, ``reason``, ``elapsed``,
            ``headers_json``, ``body``.  All values are *None* when
            *response* is *None*.
        """
        if response is None:
            return {
                "status_code": None, "reason": None, "elapsed": None,
                "headers_json": None, "body": None,
            }

        sanitized = redact_headers(dict(response.headers) if response.headers else {})
        try:
            headers_json = safe_json_dumps(sanitized, max_len=MAX_HEADERS_SIZE)
        except SecurityError:
            logger.warning("Response headers too large, storing truncated version")
            headers_json = safe_json_dumps(sanitized)[:MAX_HEADERS_SIZE] + "..."

        return {
            "status_code":  response.status_code,
            "reason":       response.reason,
            "elapsed":      response.elapsed,
            "headers_json": headers_json,
            "body":         self._prepare_body(coerce_body_to_str(response.body)) if should_capture_bodies() else None,
        }

    def truncate_error(self, error: Optional[str]) -> Optional[str]:
        """Coerce and truncate an error message string."""
        if error is None:
            return None
        text = error if isinstance(error, str) else str(error)
        if len(text) > self.MAX_ERROR_MESSAGE_LENGTH:
            return text[:self.MAX_ERROR_MESSAGE_LENGTH] + "... [TRUNCATED]"
        return text

    def decode_row(self, row: Dict[str, Any], row_id: Optional[int] = None) -> Dict[str, Any]:
        """Return a copy of *row* with header columns decoded from JSON to dicts.

        *row* is never mutated.
        """
        out = dict(row)
        for col in ("request_headers", "response_headers"):
            val = out.get(col)
            if not val:
                out[col] = {}
            else:
                try:
                    out[col] = safe_json_loads(val, row_id=row_id)
                except Exception:
                    out[col] = {}
        return out

    # ── Private helpers ───────────────────────────────────────────────────────

    def _prepare_url(self, url: str) -> str:
        if not isinstance(url, str):
            raise ValidationError("Request URL must be a string")
        if len(url) > MAX_URL_LENGTH:
            safe_preview = redact_url(url)[:100]
            logger.warning("URL too long, truncating: %s...", safe_preview)
            url = url[:self.MAX_URL_LENGTH]
        sanitized = redact_url(url)
        if sanitized != url:
            logger.info("Sensitive data detected and redacted from URL in history")
        return sanitized

    @staticmethod
    def _validate_method(method: str) -> str:
        if not isinstance(method, str):
            raise ValidationError("Request method must be a string")
        return method

    def _prepare_headers(self, headers: Dict[str, Any]) -> str:
        if not isinstance(headers, dict):
            raise ValidationError("Request headers must be a dictionary")
        return serialize_headers(headers)

    def _prepare_body(self, body: Any) -> Optional[str]:
        if body is None:
            return None
        text = body if isinstance(body, str) else str(body)
        if len(text) > _MAX_BODY:
            logger.warning(
                "Body too large, truncating from %d to %d bytes",
                len(text), _MAX_BODY,
            )
            return text[:_MAX_BODY] + "... [TRUNCATED]"
        return text

