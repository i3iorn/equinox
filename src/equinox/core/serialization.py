"""Shared serialization utilities to keep history/logging DRY."""

from __future__ import annotations

from typing import Any, Dict, Optional

from equinox.core.redact import redact_headers
from equinox.core.constants import MAX_HEADERS_SIZE, MAX_BODY_SIZE
from equinox.storage.utils import safe_json_dumps


def serialize_headers(headers: Dict[str, Any]) -> str:
    """Redact sensitive headers and JSON-dump with a max length."""
    from equinox.core.redact import redact_headers as _redact
    sanitized = _redact(headers or {})
    return safe_json_dumps(sanitized, max_len=MAX_HEADERS_SIZE)


def serialize_body(body: Any, *, max_len: int = MAX_BODY_SIZE, capture: bool = True) -> Optional[str]:
    """Serialize a body value with optional redaction toggle.

    - If capture is False, returns None (caller opted out of storing body).
    - Otherwise coerce to string and truncate to max_len, appending a truncation suffix.
    """
    if not capture:
        return None
    if body is None:
        return None
    text = body if isinstance(body, str) else str(body)
    if len(text) > max_len:
        return text[:max_len] + "... [TRUNCATED]"
    return text
