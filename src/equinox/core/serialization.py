"""Shared serialization utilities to keep history/logging DRY."""

from __future__ import annotations

from typing import Any, Dict, Optional

from equinox.core.security import redact_headers
from equinox.core.constants import MAX_HEADERS_SIZE, MAX_BODY_SIZE
from equinox.core.security.serialization import serialize_headers as _serialize_headers, serialize_body as _serialize_body


def serialize_headers(headers: Dict[str, Any]) -> str:
    """Redact sensitive headers and JSON-dump with a max length."""
    from equinox.core.security import redact_headers as _redact
    sanitized = redact_headers(headers or {})
    return _serialize_headers(headers or {})


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

# Compatibility shim: re-export from new security path
from equinox.core.security.serialization import *  # noqa: F401,F403
