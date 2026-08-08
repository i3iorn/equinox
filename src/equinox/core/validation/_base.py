"""Low-level primitives for the validation package.

Contains size/count thresholds (_Limits), pre-compiled regex patterns (_Patterns),
and shared assertion helpers (_Guards).
"""

from __future__ import annotations

import re
from typing import Any

from equinox.core.exceptions import ValidationError

__all__ = ["VALID_HTTP_METHODS", "_Limits", "_Patterns", "_Guards"]

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
    },
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


class _Patterns:
    """Namespace for pre-compiled security/format patterns."""

    SQL_INJECTION: tuple[re.Pattern[str], ...] = tuple(
        re.compile(p, re.IGNORECASE)
        for p in (
            r"(\bUNION\b.*\bSELECT\b)",
            r"(\bDROP\b.*\bTABLE\b)",
            r"(\bINSERT\b.*\bINTO\b)",
            r"(\bDELETE\b.*\bFROM\b)",
            r"(\bUPDATE\b.*\bSET\b)",
            r"(--|\#|\/\*|\*\/)",
            r"(\bOR\b.*=.*)",
            r"(;.*\b(DROP|DELETE|INSERT|UPDATE)\b)",
        )
    )

    COMMAND_INJECTION: tuple[re.Pattern[str], ...] = tuple(
        re.compile(p)
        for p in (
            r"[;&|`$]",
            r"\$\{[^}]*\}",
            r"\$\([^)]*\)",
            r"`[^`]*`",
        )
    )

    # Full XSS — used for body / general content checks.
    XSS_FULL: tuple[re.Pattern[str], ...] = tuple(
        re.compile(p, re.IGNORECASE)
        for p in (
            r"<script[^>]*>.*?</script>",
            r"javascript:",
            r"on\w+\s*=",
            r"<iframe",
            r"<object",
            r"<embed",
        )
    )

    # Reduced XSS — URL / header checks.  HTML-element patterns are excluded
    # to avoid false positives on valid API endpoint paths.
    XSS_URL: tuple[re.Pattern[str], ...] = XSS_FULL[:3]

    PATH_TRAVERSAL: tuple[re.Pattern[str], ...] = tuple(
        re.compile(p)
        for p in (
            r"\.\.[/\\]",  # ../ or ..\ anywhere in path (cross-platform)
            r"(^|[/\\])\.\.$",  # trailing .. as a path component
            r"^\.\.?$",  # bare "." or ".." as the entire path
            r"~/",  # home-relative shorthand
        )
    )

    HEADER_NAME: re.Pattern[str] = re.compile(r"^[a-zA-Z0-9!#$%&'*+\-.^_`|~]+$")
    VARIABLE_NAME: re.Pattern[str] = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
    TRAILING_COMMA_JSON: re.Pattern[str] = re.compile(r",(\s*[}\]])")


class _Guards:
    """Reusable low-level checks that don't belong to any single domain."""

    @staticmethod
    def require_nonempty_str(value: Any, field_name: str) -> None:
        """Raise ``ValidationError`` unless *value* is a non-empty ``str``."""
        if not value or not isinstance(value, str):
            raise ValidationError(f"{field_name} must be a non-empty string")

    @staticmethod
    def check_crlf(value: str, field_name: str) -> None:
        """Raise ``ValidationError`` if *value* contains CR or LF characters."""
        if "\r" in value or "\n" in value:
            raise ValidationError(f"{field_name} contains invalid characters (CRLF)")

    @staticmethod
    def check_xss_url(value: str, field_name: str) -> None:
        """Raise ``ValidationError`` if *value* matches a URL-context XSS pattern."""
        for rx in _Patterns.XSS_URL:
            if rx.search(value):
                raise ValidationError(f"{field_name} contains potentially malicious content")
