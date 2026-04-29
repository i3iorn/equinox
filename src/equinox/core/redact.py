"""Compatibility shim: re-export redaction API via the new security package.

This keeps existing imports working while centralizing redaction policy in
src/equinox/core/security. It is deprecated and will be removed in a future
release; migrate to imports from equinox.core.security.*.
"""

import warnings
warnings.warn(
    "redact.py is deprecated and will be removed in a future release. Import from equinox.core.security instead.",
    DeprecationWarning,
    stacklevel=2,
)

from __future__ import annotations

from equinox.core.security import redact_headers, redact_url, redact_body

# Re-export the historical constant for tests that import it from redact.py
SENSITIVE_HEADER_NAMES = frozenset({
    "authorization",
    "proxy-authorization",
    "x-api-key",
    "api-key",
    "apikey",
    "x-auth-token",
    "x-access-token",
    "cookie",
    "set-cookie",
    "x-csrf-token",
    "token",
    "password",
    "secret",
})

# Optional: alias for payload keys used by sanitize_details in tests
SENSITIVE_PAYLOAD_KEYS = SENSITIVE_HEADER_NAMES | frozenset({"bearer", "authorization", "credential"})

__all__ = ["redact_headers", "redact_url", "redact_body", "SENSITIVE_HEADER_NAMES", "SENSITIVE_PAYLOAD_KEYS"]
