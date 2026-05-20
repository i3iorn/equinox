"""Size/count thresholds and allowed-method constants."""

from __future__ import annotations

__all__ = ["VALID_HTTP_METHODS", "_Limits"]

#: Canonical set of allowed HTTP verbs.
VALID_HTTP_METHODS: frozenset[str] = frozenset(
    {
        "GET",
        "POST",
        "PUT",
        "PATCH",
        "DELETE",
        "HEAD",
        "OPTIONS",
        "TRACE",
        "CONNECT",
    }
)


class _Limits:
    """Centralised thresholds — single source of truth for every validator."""

    MAX_URL_LENGTH: int = 2048
    MAX_HEADER_NAME_LENGTH: int = 256
    MAX_HEADER_LENGTH: int = 8192
    MAX_HEADER_COUNT: int = 100
    MAX_BODY_SIZE: int = 100 * 1024 * 1024  # 100 MB
    MAX_PARAM_COUNT: int = 100
    MAX_PARAM_KEY_LENGTH: int = 256
    MAX_PARAM_VALUE_LENGTH: int = 4096
    MAX_VARIABLE_NAME_LENGTH: int = 128
    MAX_VARIABLE_VALUE_LENGTH: int = 4096
