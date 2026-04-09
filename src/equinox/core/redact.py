"""Centralised redaction helpers for headers, bodies, and URLs.

Every surface that renders or persists HTTP traffic (log files, GUI panels,
CLI output, export formats) should use these helpers so that sensitive data
is treated consistently.
"""

import re
from typing import Any, Dict, Optional

# =========================================================
# Constants
# =========================================================

# Display strings for redaction and masking
_REDACTED: str = "[REDACTED]"
_MASKED_CREDENTIALS: str = "***:***"
_MASKED_SHORT: str = "***"
_TRUNCATION_SUFFIX: str = "… [TRUNCATED]"
_ELLIPSIS: str = "…"

# Thresholds for masking and truncation
_DEFAULT_MASK_KEEP_CHARS: int = 8
_DEFAULT_MAX_STRING_LEN: int = 200

# Canonical set of secret keys that appear in headers, bodies, and URLs
# Used to build specific regex patterns for each context
_SENSITIVE_KEY_PATTERNS: frozenset = frozenset({
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
})

# Header names whose *values* must be fully redacted.
# Includes all sensitive keys plus HTTP-specific headers (cookies, CSRF tokens).
SENSITIVE_HEADER_NAMES: frozenset = frozenset({
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

# Payload-sensitive keys (used for auditing/sanitization) — same as _SENSITIVE_KEY_PATTERNS
# but includes HTTP-specific variants. Kept for backward compatibility.
SENSITIVE_PAYLOAD_KEYS: frozenset = _SENSITIVE_KEY_PATTERNS | frozenset({
    "bearer",
    "authorization",
    "credential",
})

# =========================================================
# Compiled regex patterns
# =========================================================

# Build the canonical key pattern string from _SENSITIVE_KEY_PATTERNS
_SECRET_KEYS_PATTERN: str = "|".join(sorted(_SENSITIVE_KEY_PATTERNS))

# Body patterns that should be masked (form-encoded secrets).
# Matches: key=value where key is a sensitive key name.
_BODY_SECRET_KEYS: re.Pattern = re.compile(
    rf"((?:{_SECRET_KEYS_PATTERN})"  # key name
    r"=)([^&\s]+)",                  # value up to next & or whitespace
    re.IGNORECASE,
)

# JSON string values: "client_secret": "…"
# Matches: "key": "value" where key is a sensitive key name.
_JSON_SECRET_KEYS: re.Pattern = re.compile(
    rf'("(?:{_SECRET_KEYS_PATTERN})"\s*:\s*")([^"]+)(")',
    re.IGNORECASE,
)

# URL embedded credentials: https://user:pass@host/
_URL_CREDENTIALS: re.Pattern = re.compile(
    r"(https?://)([^@/:]+):([^@/]+)@",
    re.IGNORECASE,
)

# URL query-string secret parameters: ?key=value
# Matches: ?key=value or &key=value where key is a sensitive key name.
_URL_SECRET_PARAMS: re.Pattern = re.compile(
    rf"((?:\?|&)(?:{_SECRET_KEYS_PATTERN})"
    r"=)([^&#]+)",
    re.IGNORECASE,
)

# =========================================================
# Redaction functions
# =========================================================

def redact_headers(headers: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Return a copy of *headers* with sensitive values replaced by ``[REDACTED]``.

    Identifies sensitive headers by name (case-insensitive) using SENSITIVE_HEADER_NAMES.
    Other headers are passed through unchanged.

    Args:
        headers: Dictionary of header names to values (or None).

    Returns:
        New dictionary with sensitive header values redacted.
    """
    if not headers:
        return {}
    return {
        k: (_REDACTED if k.lower() in SENSITIVE_HEADER_NAMES else v)
        for k, v in headers.items()
    }


def redact_body(body: Optional[str], *, max_length: int = 0) -> Optional[str]:
    """Mask credential-like values inside a request/response body string.

    Handles both form-encoded (``key=val&…``) and JSON (``"key": "val"``)
    patterns. The body is returned in full unless *max_length* > 0, in which
    case it is truncated afterward.

    Uses _SENSITIVE_KEY_PATTERNS to identify secrets uniformly across both
    encoding styles.

    Args:
        body: Request or response body text (or None).
        max_length: Optional maximum length; if exceeded, body is truncated
                    with _TRUNCATION_SUFFIX appended (default 0, no limit).

    Returns:
        Redacted body string, or original body if None/empty.
    """
    if not body:
        return body
    result = _BODY_SECRET_KEYS.sub(r"\g<1>" + _REDACTED, body)
    result = _JSON_SECRET_KEYS.sub(r"\g<1>" + _REDACTED + r"\3", result)
    if max_length and len(result) > max_length:
        result = result[:max_length] + _TRUNCATION_SUFFIX
    return result


def redact_url(url: str) -> str:
    """Strip embedded credentials and secret query params from a URL.

    Redacts:
    - Embedded basic auth credentials (https://user:pass@host/)
    - Query parameters for sensitive keys (/?api_key=xxx)

    Uses _SENSITIVE_KEY_PATTERNS to identify secret query parameters uniformly.

    Args:
        url: URL string to redact (or empty string).

    Returns:
        Redacted URL with credentials and secret params masked.
    """
    if not url:
        return url
    result = _URL_CREDENTIALS.sub(r"\g<1>" + _MASKED_CREDENTIALS + "@", url)
    result = _URL_SECRET_PARAMS.sub(r"\g<1>" + _REDACTED, result)
    return result


def mask_secret(
    value: Optional[str],
    *,
    keep: int = _DEFAULT_MASK_KEEP_CHARS,
) -> str:
    """Return a safe display preview of a secret string.

    Shows the first *keep* characters followed by _ELLIPSIS when the secret is
    long enough, otherwise returns _MASKED_SHORT to indicate it's a secret.

    Useful for displaying secrets in UI labels and logs where you want to avoid
    leaking the full value but may show a preview for debugging purposes.

    Args:
        value: The secret string to preview (``None`` becomes _MASKED_SHORT).
        keep: Number of leading characters to keep visible
              (default _DEFAULT_MASK_KEEP_CHARS).

    Returns:
        A short preview string safe for display in logs and UI labels.
    """
    if not value:
        return _MASKED_SHORT
    if len(value) > keep:
        return value[:keep] + _ELLIPSIS
    return _MASKED_SHORT


def sanitize_details(
    details: dict,
    *,
    max_string_len: int = _DEFAULT_MAX_STRING_LEN,
) -> dict:
    """Return a sanitized copy of *details* by redacting sensitive keys and
    truncating long strings.

    This is recursive and will preserve non-sensitive structure while
    redacting values whose keys match entries from SENSITIVE_PAYLOAD_KEYS.

    String values exceeding *max_string_len* are truncated with "…" appended.

    Args:
        details: Dictionary to sanitize (may be nested with lists/dicts).
        max_string_len: Maximum length for string values before truncation
                       (default _DEFAULT_MAX_STRING_LEN).

    Returns:
        New dictionary tree with sensitive values redacted and long strings truncated.
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


