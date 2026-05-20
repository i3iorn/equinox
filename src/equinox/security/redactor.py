"""Redaction engine moved from redact.py. See tests for behavior.
This file now contains the actual implementation; other modules should import
from equinox.security.redactor import redact_headers, redact_body, redact_url.
"""

from __future__ import annotations

import re
from typing import Any

_REDACTED: str = "[REDACTED]"
_MASKED_CREDENTIALS: str = "***:***"
_MASKED_SHORT: str = "***"
_TRUNCATION_SUFFIX: str = "… [TRUNCATED]"
_ELLIPSIS: str = "…"

_DEFAULT_MASK_KEEP_CHARS: int = 8
_DEFAULT_MAX_STRING_LEN: int = 200

_SENSITIVE_KEY_PATTERNS: frozenset = frozenset(
    {
        "client_secret",
        "client_password",
        "password",
        "secret",
        "access_token",
        "refresh_token",
        "token",
        "api_key",
        "apikey",
        "private_key",
        "authorization",
        "bearer",
        "credential",
    }
)

SENSITIVE_HEADER_NAMES: frozenset = frozenset(
    {
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
    }
)

SENSITIVE_PAYLOAD_KEYS: frozenset = _SENSITIVE_KEY_PATTERNS | frozenset(
    {
        "bearer",
        "authorization",
        "credential",
    }
)

_SECRET_KEYS_PATTERN: str = "|".join(sorted(_SENSITIVE_KEY_PATTERNS))
_BODY_SECRET_KEYS: re.Pattern = re.compile(
    rf"((?:{_SECRET_KEYS_PATTERN})=)([^&\s]+)", re.IGNORECASE
)
_JSON_SECRET_KEYS: re.Pattern = re.compile(
    rf'("(?:{_SECRET_KEYS_PATTERN})"\s*:\s*")([^\"]+)(")', re.IGNORECASE
)
_URL_CREDENTIALS: re.Pattern = re.compile(r"(https?://)([^@/:]+):([^@/]+)@", re.IGNORECASE)
_URL_SECRET_PARAMS: re.Pattern = re.compile(
    rf"((?:\?|&)(?:{_SECRET_KEYS_PATTERN})=)([^&#]+)", re.IGNORECASE
)


def redact_headers(headers: dict[str, Any]) -> dict[str, Any | None]:
    if not headers:
        return {}
    return {
        k: (_REDACTED if k.lower() in SENSITIVE_HEADER_NAMES else v) for k, v in headers.items()
    }


def redact_body(body: str | None, *, max_length: int = 0, max_len: int = 0) -> str | None:
    if not body:
        return body

    # Support both max_length and max_len for backward compatibility and DRY surface
    limit = max_length or max_len

    result = _BODY_SECRET_KEYS.sub(r"\g<1>" + _REDACTED, body)
    result = _JSON_SECRET_KEYS.sub(r"\g<1>" + _REDACTED + r"\3", result)
    if limit and len(result) > limit:
        result = result[:limit] + _TRUNCATION_SUFFIX
    return result


def redact_url(url: str | None) -> str | None:
    if url is None:
        return None
    s = _URL_CREDENTIALS.sub(r"\g<1>" + _MASKED_CREDENTIALS + "@", url)
    s = _URL_SECRET_PARAMS.sub(r"\g<1>" + _REDACTED, s)
    return s


def mask_secret(value: str | None, *, keep: int = _DEFAULT_MASK_KEEP_CHARS) -> str:
    if not value:
        return _MASKED_SHORT
    if len(value) > keep:
        return value[:keep] + _ELLIPSIS
    return _MASKED_SHORT


def sanitize_details(details: dict, *, max_string_len: int = _DEFAULT_MAX_STRING_LEN) -> dict:
    def _sanitize(obj):
        if isinstance(obj, dict):
            out = {}
            for k, v in obj.items():
                k_lower = k.lower()
                if any(s in k_lower for s in SENSITIVE_PAYLOAD_KEYS):
                    out[k] = _REDACTED
                else:
                    out[k] = _sanitize(v)
            return out
        if isinstance(obj, (list, tuple)):
            return [_sanitize(i) for i in obj]
        if isinstance(obj, str):
            if len(obj) > max_string_len:
                return obj[:max_string_len] + "..."
            return obj
        return obj

    return _sanitize(details)
