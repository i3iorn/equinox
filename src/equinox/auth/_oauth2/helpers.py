"""Helper functions for OAuth2 authentication."""

from __future__ import annotations

import base64
from typing import Any

import httpx

from equinox.auth._base import AuthError
from equinox.auth._oauth2.constants import (
    _CONNECTION_REFUSED_MARKERS,
    _REDACTABLE_TOKEN_FIELDS,
    _TOKEN_REDACT_MIN_LEN,
    _TOKEN_REDACT_PREFIX_LEN,
    _TOKEN_REDACT_SUFFIX_LEN,
)


def make_oauth2_basic_auth_header(client_id: str, client_secret: str) -> str:
    """Return an RFC 6749 HTTP Basic Authorization header value."""
    if not client_id or not client_secret:
        raise AuthError("Client ID and secret are required for Basic auth token endpoint")
    credentials = f"{client_id}:{client_secret}"
    encoded = base64.b64encode(credentials.encode("utf-8")).decode("ascii")
    return f"Basic {encoded}"


def is_connection_refused(exc: Exception) -> bool:
    """Return True when the exception indicates a closed destination port."""
    if not isinstance(exc, httpx.ConnectError):
        return False
    lower = str(exc).lower()
    return any(marker in lower for marker in _CONNECTION_REFUSED_MARKERS)


def redact_token_value(key: str, value: Any) -> Any:
    """Return a short preview for token fields while leaving other values unchanged."""
    if (
        key in _REDACTABLE_TOKEN_FIELDS
        and isinstance(value, str)
        and len(value) > _TOKEN_REDACT_MIN_LEN
    ):
        return value[:_TOKEN_REDACT_PREFIX_LEN] + "..." + value[-_TOKEN_REDACT_SUFFIX_LEN:]
    return value


def credential_diagnostics(value: str | None) -> dict[str, Any]:
    """Return non-sensitive diagnostics about a credential string."""
    if value is None:
        return {
            "is_present": False,
            "length": 0,
            "trimmed_length": 0,
            "has_outer_whitespace": False,
        }

    trimmed = value.strip()
    return {
        "is_present": True,
        "length": len(value),
        "trimmed_length": len(trimmed),
        "has_outer_whitespace": value != trimmed,
    }


def token_error_code(response: httpx.Response | None) -> str:
    """Return OAuth2 token error code from response JSON, or an empty string."""
    if response is None:
        return ""
    try:
        payload = response.json()
    except Exception:
        return ""
    if not isinstance(payload, dict):
        return ""
    err = payload.get("error")
    return str(err).strip().lower() if err is not None else ""
