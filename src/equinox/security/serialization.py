"""Shared serialization utilities moved under security namespace.

This module provides the same API as the old core.serialization helpers,
but is now the single source of truth for redaction-aware serialization
used by history and logging paths.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from equinox.security.redactor import redact_headers, redact_url
from equinox.core.util.constants import MAX_HEADERS_SIZE, MAX_BODY_SIZE
from equinox.storage.utils import safe_json_dumps


def serialize_headers(headers: Dict[str, Any]) -> str:
    sanitized = redact_headers(headers or {})
    return safe_json_dumps(sanitized, max_len=MAX_HEADERS_SIZE)


def serialize_body(body: Any, *, max_len: int = MAX_BODY_SIZE, capture: bool = True) -> Optional[str]:
    if not capture:
        return None
    if body is None:
        return None
    text = body if isinstance(body, str) else str(body)
    if len(text) > max_len:
        return text[:max_len] + "... [TRUNCATED]"
    return text
