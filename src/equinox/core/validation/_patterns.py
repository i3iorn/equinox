"""Pre-compiled regex patterns shared across all validators.

Compiling once at import time avoids repeated ``re.compile`` overhead on every
validation call.
"""

from __future__ import annotations

import re

__all__ = ["_Patterns"]


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
