"""HTTP header name and value validation."""

from __future__ import annotations

import logging

from equinox.core.exceptions import ValidationError

from ._base import _Guards, _Limits, _Patterns

__all__ = ["_HeaderValidator"]

_logger = logging.getLogger(__name__)


class _HeaderValidator:
    """HTTP header name and value validation."""

    # Headers managed by httpx — overriding them may cause issues, but an API
    # testing tool should allow it with a warning when ``strict=False``.
    _MANAGED: frozenset[str] = frozenset(
        {
            "host",
            "connection",
            "content-length",
            "transfer-encoding",
            "upgrade",
        }
    )

    @classmethod
    def validate_name(cls, name: str, *, strict: bool = True) -> str:
        """Validate a single header name.

        Args:
            strict: When *True* (default) transport-managed headers are
                rejected.  Pass ``strict=False`` on the send path so users
                can intentionally override them; a warning is logged instead.
        """
        _Guards.require_nonempty_str(name, "Header name")
        name = name.strip()

        if len(name) > _Limits.MAX_HEADER_NAME_LENGTH:
            raise ValidationError("Header name too long")

        if not _Patterns.HEADER_NAME.match(name):
            raise ValidationError(f"Invalid header name format: {name}")

        lower = name.lower()
        if lower in cls._MANAGED:
            if strict:
                raise ValidationError(f"Cannot manually set header: {name}")
            _logger.warning(
                "Header '%s' is normally managed by the HTTP transport layer "
                "— overriding it may cause unexpected behaviour.",
                name,
            )

        return name

    @classmethod
    def validate_value(cls, value: str) -> str:
        if not isinstance(value, str):
            raise ValidationError("Header value must be a string")

        if len(value) > _Limits.MAX_HEADER_LENGTH:
            raise ValidationError("Header value too long")

        _Guards.check_crlf(value, "Header value")
        _Guards.check_xss_url(value, "Header value")

        return value

    @classmethod
    def validate_all(
        cls,
        headers: dict[str, str],
        *,
        strict: bool = True,
    ) -> dict[str, str]:
        if not isinstance(headers, dict):
            raise ValidationError("Headers must be a dictionary")

        if len(headers) > _Limits.MAX_HEADER_COUNT:
            raise ValidationError(f"Too many headers (max: {_Limits.MAX_HEADER_COUNT})")

        return {
            cls.validate_name(name, strict=strict): cls.validate_value(str(value))
            for name, value in headers.items()
        }
