"""Security helpers and redaction policy facade.

This package centralizes redaction and security-related helpers so that every
module (GUI, CLI, history, logging, etc.) imports a single source of truth for
redaction logic.
"""

from __future__ import annotations

# Re-export the main redaction wrappers
from .redactor import redact_headers, redact_url, redact_body  # noqa: F401
from .redactor import mask_secret, sanitize_details, SENSITIVE_HEADER_NAMES, SENSITIVE_PAYLOAD_KEYS  # noqa: F401
from .crypto import get_or_create_raw_key, make_fernet, get_or_create_fernet  # noqa: F401
from .secure_storage import SecureStorage  # noqa: F401

__all__ = [
    "redact_headers", "redact_url", "redact_body",
    "mask_secret", "sanitize_details", "SENSITIVE_HEADER_NAMES", "SENSITIVE_PAYLOAD_KEYS",
    "get_or_create_raw_key", "make_fernet", "get_or_create_fernet", "SecureStorage"
]
