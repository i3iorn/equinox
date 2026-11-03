"""Redaction engine moved from redact.py. See tests for behavior.
This file now contains the actual implementation; other modules should import
from equinox.security.redactor import redact_headers, redact_body, redact_url.
"""

from __future__ import annotations

import re
import urlps
from typing import Any

_REDACTED: str = "[REDACTED]"
_MASKED_SHORT: str = "***"
_TRUNCATION_SUFFIX: str = "… [TRUNCATED]"
_ELLIPSIS: str = "…"

_DEFAULT_MASK_KEEP_CHARS: int = 4
_DEFAULT_MAX_STRING_LEN: int = 200

_SENSITIVE_KEY_PATTERNS: frozenset[str] = frozenset(
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
    },
)

SENSITIVE_HEADER_NAMES: frozenset[str] = frozenset(
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
    },
)

SENSITIVE_PAYLOAD_KEYS: frozenset[str] = _SENSITIVE_KEY_PATTERNS | frozenset(
    {
        "bearer",
        "authorization",
        "credential",
    },
)

_SECRET_KEYS_PATTERN: str = "|".join(sorted(_SENSITIVE_KEY_PATTERNS))
_BODY_SECRET_KEYS: re.Pattern[str] = re.compile(
    rf"((?:{_SECRET_KEYS_PATTERN})=)([^&\s]+)",
    re.IGNORECASE,
)
_JSON_SECRET_KEYS: re.Pattern[str] = re.compile(
    rf'("(?:{_SECRET_KEYS_PATTERN})"\s*:\s*")([^\"]+)(")',
    re.IGNORECASE,
)

# JWT-shaped substrings: `eyJ` base64url header + two dot-separated segments
# with base64url characters. Masked regardless of field name so a JWT inside
# a free-form error message or a non-standard field (issue #69) is caught.
_JWT_SHAPE: re.Pattern[str] = re.compile(
    r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+",
)
_MASKED_JWT: str = "eyJ[REDACTED-JWT]"


def mask_jwt_shaped(text: str) -> str:
    """Mask JWT-shaped substrings regardless of field name (issue #69)."""
    if not text:
        return text
    return _JWT_SHAPE.sub(_MASKED_JWT, text)


def redact_headers(headers: dict[str, Any]) -> dict[str, Any | None]:
    if not headers:
        return {}
    return {
        k: (_REDACTED if k.lower() in SENSITIVE_HEADER_NAMES else v) for k, v in headers.items()
    }


def redact_params(params: dict[str, Any]) -> dict[str, Any | None]:
    """Redact query-parameter values whose keys match sensitive patterns.

    Same policy as :func:`redact_headers` but keyed on
    :data:`SENSITIVE_PAYLOAD_KEYS` (API-key-in-query, tokens, secrets, etc.)
    so exporters and payload builders never carry live credentials by key
    name.
    """
    if not params:
        return {}
    return {k: (_REDACTED if k.lower() in SENSITIVE_PAYLOAD_KEYS else v) for k, v in params.items()}


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


def _decode_urlps_masks(sanitized: str) -> str:
    """Normalize urlps' mask tokens to Equinox's ``[REDACTED]`` vocabulary.

    urlps marks masked query values as ``%2A%2A%2A``; Equinox renders all
    redactions as ``[REDACTED]`` (history UI, exports, logs), so the token
    is translated for a consistent display contract. Userinfo passwords are
    masked as ``***`` by urlps, which matches Equinox's own short-mask.
    """
    return sanitized.replace("%2A%2A%2A", _REDACTED)


# urlps' built-in sensitive-key set does not include ``bearer`` (urlps
# issue #74 discussion); everything else Equinox masks is covered upstream.
_URL_EXTRA_SENSITIVE_KEYS: tuple[str, ...] = ("bearer",)


def redact_url(url: str | None) -> str | None:
    """Offload string-level URL redaction to urlps (``redact_url_for_logs``).

    Works on the raw string, so URLs that fail host grammar (``{{host}}``
    templates, malformed input) are still masked instead of leaking, and
    URLs with nothing to mask pass through byte-identical. ``bearer`` is
    provided as an extra sensitive key because urlps' built-in set does not
    cover it; JWT-shaped substrings under non-sensitive keys are masked
    locally (issue #69).
    """
    if url is None:
        return None
    sanitized = urlps.redact_url_for_logs(url, extra_sensitive_keys=_URL_EXTRA_SENSITIVE_KEYS)
    return mask_jwt_shaped(_decode_urlps_masks(sanitized))


def mask_secret(value: str | None, *, keep: int = _DEFAULT_MASK_KEEP_CHARS) -> str:
    if not value:
        return _MASKED_SHORT
    if len(value) > keep:
        return value[:keep] + _ELLIPSIS
    return _MASKED_SHORT


def sanitize_details(
    details: dict[str, Any],
    *,
    max_string_len: int = _DEFAULT_MAX_STRING_LEN,
) -> dict[str, Any]:
    def _sanitize(obj: Any) -> Any:
        if isinstance(obj, dict):
            out: dict[str, Any] = {}
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

    sanitized = _sanitize(details)
    if isinstance(sanitized, dict):
        return sanitized
    return {}
