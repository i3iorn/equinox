"""Low-level assertion helpers shared across all domain validators."""
from __future__ import annotations

from typing import Any

from equinox.core.exceptions import ValidationError
from ._patterns import _Patterns

__all__ = ["_Guards"]


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
            raise ValidationError(
                f"{field_name} contains invalid characters (CRLF)"
            )

    @staticmethod
    def check_xss_url(value: str, field_name: str) -> None:
        """Raise ``ValidationError`` if *value* matches a URL-context XSS pattern."""
        for rx in _Patterns.XSS_URL:
            if rx.search(value):
                raise ValidationError(
                    f"{field_name} contains potentially malicious content"
                )

