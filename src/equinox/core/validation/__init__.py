"""Input validation and sanitization package.

Provides comprehensive zero-trust validation for all user inputs.

Public API
----------
- ``VALID_HTTP_METHODS`` — frozenset of allowed HTTP method strings
- ``Validator``          — façade with ``validate_*`` / ``sanitize_*`` class-methods

Internal structure (one concern per module)
-------------------------------------------
  _limits    — ``_Limits``         — all size/count thresholds + ``VALID_HTTP_METHODS``
  _patterns  — ``_Patterns``       — pre-compiled regex patterns
  _guards    — ``_Guards``         — low-level assertion helpers
  _ssrf      — ``_SsrfGuard``      — SSRF / private-network protection (+ ``_DnsPool``)
  _url       — ``_UrlValidator``   — URL string and structural validation
  _headers   — ``_HeaderValidator``— header name/value validation
  _body      — ``_BodyValidator``  — request body validation
  _params    — ``_ParamValidator`` — query-parameter validation
  _path      — ``_PathValidator``  — file-path / traversal validation
  _env       — ``_EnvVarValidator``— environment-variable name/value validation
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ._base import VALID_HTTP_METHODS, _Guards, _Limits, _Patterns
from ._body import _BodyValidator
from ._env import _EnvVarValidator
from ._headers import _HeaderValidator
from ._params import _ParamValidator
from ._path import _PathValidator
from ._ssrf import _SsrfGuard
from ._url import _UrlValidator

__all__ = ["VALID_HTTP_METHODS", "Validator"]


# ---------------------------------------------------------------------------
# Validator — public façade (backward-compatible API)
# ---------------------------------------------------------------------------


class Validator:
    """Zero-trust input validator.

    This class is a thin façade over focused internal validators.
    All ``validate_*`` methods maintain full backward compatibility.
    """

    # -- Limits (re-exposed for backward compatibility) -----------------------

    VALID_URL_SCHEMES = _UrlValidator.VALID_SCHEMES
    MAX_URL_LENGTH = _Limits.MAX_URL_LENGTH
    MAX_HEADER_LENGTH = _Limits.MAX_HEADER_LENGTH
    MAX_BODY_SIZE = _Limits.MAX_BODY_SIZE
    MAX_HEADER_COUNT = _Limits.MAX_HEADER_COUNT
    MAX_PARAM_COUNT = _Limits.MAX_PARAM_COUNT
    MAX_PARAM_KEY_LENGTH = _Limits.MAX_PARAM_KEY_LENGTH
    MAX_PARAM_VALUE_LENGTH = _Limits.MAX_PARAM_VALUE_LENGTH
    MAX_VARIABLE_NAME_LENGTH = _Limits.MAX_VARIABLE_NAME_LENGTH
    MAX_VARIABLE_VALUE_LENGTH = _Limits.MAX_VARIABLE_VALUE_LENGTH

    # -- URL ------------------------------------------------------------------

    @classmethod
    def validate_url(cls, url: str) -> str:
        """String-level URL validation; ``{{placeholders}}`` are permitted."""
        return _UrlValidator.validate(url)

    @classmethod
    def validate_resolved_url(cls, url: str) -> str:
        """Full structural URL validation after placeholder expansion."""
        return _UrlValidator.validate_resolved(url)

    # -- Headers --------------------------------------------------------------

    @classmethod
    def validate_header_name(cls, name: str, *, strict: bool = True) -> str:
        return _HeaderValidator.validate_name(name, strict=strict)

    @classmethod
    def validate_header_value(cls, value: str) -> str:
        return _HeaderValidator.validate_value(value)

    @classmethod
    def validate_variable_name(cls, name: str) -> str:
        """Validate an interpolation/session variable name.

        Equinox uses ``{{name}}`` placeholders in many GUI contexts, so this
        keeps the accepted naming rules centralized instead of duplicating a
        panel-local regex in multiple widgets.
        """
        _Guards.require_nonempty_str(name, "Variable name")
        name = name.strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
            from equinox.core.exceptions import ValidationError

            raise ValidationError(
                "Variable names may contain only letters, numbers, underscore, and hyphen."
            )
        if len(name) > _Limits.MAX_VARIABLE_NAME_LENGTH:
            from equinox.core.exceptions import ValidationError

            raise ValidationError("Variable name too long")
        return name

    @classmethod
    def validate_cookie_name(cls, name: str) -> str:
        """Validate a cookie name before persisting or copying it."""
        _Guards.require_nonempty_str(name, "Cookie name")
        name = name.strip()
        if not _Patterns.HEADER_NAME.match(name):
            from equinox.core.exceptions import ValidationError

            raise ValidationError("Cookie name contains invalid characters")
        return name

    @classmethod
    def validate_cookie_value(cls, value: Any) -> str:
        """Validate a cookie value for display and storage."""
        if not isinstance(value, str):
            from equinox.core.exceptions import ValidationError

            raise ValidationError("Cookie value must be a string")
        if len(value) > _Limits.MAX_HEADER_LENGTH:
            from equinox.core.exceptions import ValidationError

            raise ValidationError("Cookie value too long")
        return _HeaderValidator.validate_value(value)

    @classmethod
    def validate_headers(cls, headers: dict[str, str], *, strict: bool = True) -> dict[str, str]:
        return _HeaderValidator.validate_all(headers, strict=strict)

    # -- Body -----------------------------------------------------------------

    @classmethod
    def validate_request_body(cls, body: Any, content_type: str | None = None) -> Any:
        return _BodyValidator.validate(body, content_type)

    # -- Query parameters -----------------------------------------------------

    @classmethod
    def validate_query_params(cls, params: dict[str, Any]) -> dict[str, Any]:
        return _ParamValidator.validate(params)

    # -- File path ------------------------------------------------------------

    @classmethod
    def validate_file_path(cls, path: str, base_dir: Path | None = None) -> Path:
        return _PathValidator.validate(path, base_dir)

    # -- Environment variable -------------------------------------------------

    @classmethod
    def validate_environment_variable(cls, name: str, value: str) -> tuple[str, str]:
        return _EnvVarValidator.validate(name, value)

    # -- HTTP method ----------------------------------------------------------

    @classmethod
    def validate_method(cls, method: str) -> str:
        _Guards.require_nonempty_str(method, "HTTP method")
        method = method.upper().strip()
        if method not in VALID_HTTP_METHODS:
            from equinox.core.exceptions import ValidationError

            raise ValidationError(f"Invalid HTTP method: {method}")
        return method

    # -- Display sanitization -------------------------------------------------

    @classmethod
    def sanitize_for_display(cls, text: Any, max_length: int = 1000) -> str:
        if not isinstance(text, str):
            text = str(text)
        if len(text) > max_length:
            text = text[:max_length] + "..."
        # Strip control characters while preserving newlines (\n) and tabs (\t).
        return re.sub(r"[\x00-\x08\x0B-\x0C\x0E-\x1F\x7F]", "", text)

    # -- SSRF (Hostname validation) -------------------------------------------

    @classmethod
    def validate_hostname(cls, hostname: str) -> str:
        """Raise ``ValidationError`` if *hostname* targets a private network."""
        _Guards.require_nonempty_str(hostname, "Hostname")
        _SsrfGuard.check(hostname)
        return hostname

    @classmethod
    def _check_ssrf(cls, hostname: str) -> None:
        """Deprecated: use ``validate_hostname`` instead."""
        cls.validate_hostname(hostname)
