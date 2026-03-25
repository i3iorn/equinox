"""Centralised redaction helpers for headers, bodies, and URLs.

Every surface that renders or persists HTTP traffic (log files, GUI panels,
CLI output, export formats) should use these helpers so that sensitive data
is treated consistently.
"""

import re
from typing import Any, Dict, Optional

# Header names whose *values* must be fully redacted.
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

_REDACTED = "[REDACTED]"

# Body patterns that should be masked (form-encoded secrets).
_BODY_SECRET_KEYS = re.compile(
    r"((?:client_secret|client_password|password|secret|access_token|refresh_token"
    r"|token|api_key|apikey)"       # key name
    r"=)([^&\s]+)",                  # value up to next & or whitespace
    re.IGNORECASE,
)

# Same idea for JSON string values:  "client_secret": "…"
_JSON_SECRET_KEYS = re.compile(
    r'("(?:client_secret|client_password|password|secret|access_token|refresh_token'
    r'|token|api_key|apikey)"\s*:\s*")([^"]+)(")',
    re.IGNORECASE,
)

# URL embedded credentials:  https://user:pass@host/
_URL_CREDENTIALS = re.compile(r"(https?://)([^@/:]+):([^@/]+)@", re.IGNORECASE)

# URL query-string secret parameters
_URL_SECRET_PARAMS = re.compile(
    r"((?:\?|&)(?:api_key|apikey|token|access_token|refresh_token|secret|password"
    r"|client_secret|private_key|authorization)"
    r"=)([^&#]+)",
    re.IGNORECASE,
)


def redact_headers(headers: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Return a copy of *headers* with sensitive values replaced by ``[REDACTED]``."""
    if not headers:
        return {}
    return {
        k: (_REDACTED if k.lower() in SENSITIVE_HEADER_NAMES else v)
        for k, v in headers.items()
    }


def redact_body(body: Optional[str], *, max_length: int = 0) -> Optional[str]:
    """Mask credential-like values inside a request/response body string.

    Handles both form-encoded (``key=val&…``) and JSON (``"key": "val"``)
    patterns.  The body is returned in full unless *max_length* > 0, in which
    case it is truncated afterward.
    """
    if not body:
        return body
    result = _BODY_SECRET_KEYS.sub(r"\g<1>" + _REDACTED, body)
    result = _JSON_SECRET_KEYS.sub(r"\g<1>" + _REDACTED + r"\3", result)
    if max_length and len(result) > max_length:
        result = result[:max_length] + "… [TRUNCATED]"
    return result


def redact_url(url: str) -> str:
    """Strip embedded credentials and secret query params from a URL."""
    if not url:
        return url
    result = _URL_CREDENTIALS.sub(r"\g<1>***:***@", url)
    result = _URL_SECRET_PARAMS.sub(r"\g<1>" + _REDACTED, result)
    return result


# Payload-sensitive keys (used for auditing/sanitization)
SENSITIVE_PAYLOAD_KEYS = frozenset({
    "password",
    "token",
    "secret",
    "api_key",
    "bearer",
    "authorization",
    "credential",
    "private_key",
    "client_secret",
    "access_token",
    "refresh_token",
})


def sanitize_details(details: dict, *, max_string_len: int = 200) -> dict:
    """Return a sanitized copy of *details* by redacting sensitive keys and
    truncating long strings.

    This is recursive and will preserve non-sensitive structure while
    redacting values whose keys match entries from
    :data:`SENSITIVE_PAYLOAD_KEYS`.
    """
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


