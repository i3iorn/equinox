"""URL string and structural validation."""
from __future__ import annotations

import logging

from equinox.core import urls
from equinox.core.exceptions import ValidationError
from ._base import _Limits, _Guards
from ._ssrf import _SsrfGuard

__all__ = ["_UrlValidator"]

_logger = logging.getLogger(__name__)

_VALID_SCHEMES: frozenset[str] = frozenset({"http", "https"})


class _UrlValidator:
    """URL validation — string-level and fully-resolved variants."""

    VALID_SCHEMES: frozenset[str] = _VALID_SCHEMES

    @classmethod
    def validate(cls, url: str) -> str:
        """String-level checks only; ``{{placeholders}}`` are permitted.

        Use this during import/construction before variables are resolved.
        """
        _Guards.require_nonempty_str(url, "URL")
        url = url.strip()

        if len(url) > _Limits.MAX_URL_LENGTH:
            _logger.warning(
                "URL validation failed: exceeds max length",
                extra={"length": len(url), "max_length": _Limits.MAX_URL_LENGTH},
            )
            raise ValidationError(
                f"URL exceeds maximum length of {_Limits.MAX_URL_LENGTH}"
            )

        _Guards.check_xss_url(url, "URL")

        _logger.debug("URL validation passed", extra={"url_length": len(url)})
        return url

    @classmethod
    def validate_resolved(cls, url: str) -> str:
        """Full structural validation after all ``{{placeholders}}`` are expanded.

        Call this at send-time when the URL is fully resolved.
        """
        url = cls.validate(url)

        expanded = urls.expand_placeholders(url, None)
        try:
            parts = urls.normalized_parts(expanded)
        except Exception as exc:
            _logger.warning(
                "URL parsing failed via urls.normalized_parts",
                extra={"error": str(exc), "url_length": len(expanded)},
            )
            raise ValidationError(f"Invalid URL format: {exc}")

        scheme = parts.get("scheme") or ""
        netloc = parts.get("netloc") or ""

        if scheme not in cls.VALID_SCHEMES:
            _logger.warning(
                "URL validation failed: invalid scheme",
                extra={"scheme": scheme, "allowed_schemes": sorted(cls.VALID_SCHEMES)},
            )
            raise ValidationError(
                f"Invalid URL scheme '{scheme}'. "
                f"Allowed: {', '.join(sorted(cls.VALID_SCHEMES))}"
            )

        if not netloc:
            _logger.warning("URL validation failed: missing hostname")
            raise ValidationError("URL must contain a hostname")

        parsed_host = urls.url_metadata(expanded).get("hostname")
        if parsed_host:
            _SsrfGuard.check(parsed_host)

        _logger.debug(
            "URL validation passed",
            extra={"scheme": scheme, "host": parsed_host or "unknown"},
        )
        return url

