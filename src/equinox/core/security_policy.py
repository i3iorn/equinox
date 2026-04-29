"""Security policy surface for redaction and related decisions.

This module provides a single place to evaluate and apply security-related
decisions (e.g., what to redact, what to reveal) so that other components
do not diverge in their policies. Right now it re-exports existing redaction
helpers but provides a stable API for future hardening.
"""

from __future__ import annotations

from typing import Dict, Any, Optional

from equinox.core.redact import redact_headers as _redact_headers
from equinox.core.redact import redact_url as _redact_url
from equinox.core.redact import redact_body as _redact_body


def redact_headers(headers: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    return _redact_headers(headers or {})


def redact_url(url: Optional[str]) -> Optional[str]:
    if url is None:
        return None
    return _redact_url(url)


def redact_body(body: Optional[str], max_len: int = 1000) -> Optional[str]:
    if body is None:
        return None
    return _redact_body(body, max_length=max_len)  # type: ignore[arg-type]
