"""Base authentication strategy.

Provides the ``AuthStrategy`` abstract base class and shared validation.

Every concrete strategy must define:

- ``AUTH_TYPE``      — short identifier (e.g. ``"bearer"``, ``"basic"``).
                       MUST be globally unique across all auth implementations.
- ``DISPLAY_NAME``   — human-readable label for GUI rendering.
- ``apply()``        — inject credentials into request headers/params.
- ``to_dict()``      — round-trip serialisation.
- ``from_dict()``    — deserialisation from a dict.

Optional overrides for richer behaviour:

- ``interpolate()``  — return a copy with ``{{VAR}}`` placeholders expanded.
- ``get_display_summary()``  — one-liner summary for the GUI auth tab.
- ``get_preflight_warning()``  — advisory string when required fields are empty.
- ``interpolate_fields()``  — override for custom interpolation strategy.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, Optional

from equinox.core.exceptions import AuthError

logger = logging.getLogger(__name__)

# Maximum length for any single credential value (tokens, passwords, keys).
_MAX_CREDENTIAL_LENGTH = 16_384


AUTH_TYPE_LABELS: Dict[str, str] = {
    "oauth2":    "OAuth 2.0",
    "api_key":   "API Key",
    "basic":     "Basic Auth",
    "bearer":    "Bearer Token",
    "aws_sigv4": "AWS SigV4",
}

class CredentialValidationError(AuthError):
    """Raised when credential validation fails.

    Attributes:
        field_name: Name of the field that failed validation.
        reason: Specific reason for the validation failure.
    """

    def __init__(self, field_name: str, reason: str):
        self.field_name = field_name
        self.reason = reason
        super().__init__(f"{field_name}: {reason}")


def _validate_credential(value: str, field_name: str) -> str:
    """Validate a credential string for common security problems.

    Checks:
    - Must be a non-empty ``str``.
    - Must not exceed :data:`_MAX_CREDENTIAL_LENGTH`.
    - Must not contain ``\\r`` or ``\\n`` (CRLF header-injection prevention).

    Args:
        value: The credential string to validate.
        field_name: Human-readable label used in error messages.

    Returns:
        The validated string.

    Raises:
        CredentialValidationError: If validation fails with specific reason.
    """
    if not isinstance(value, str) or not value:
        raise CredentialValidationError(field_name, "must be a non-empty string")

    if len(value) > _MAX_CREDENTIAL_LENGTH:
        raise CredentialValidationError(
            field_name,
            f"exceeds maximum length ({len(value)} > {_MAX_CREDENTIAL_LENGTH})"
        )

    if "\r" in value or "\n" in value:
        logger.warning("CRLF injection attempt detected in %s", field_name)
        raise CredentialValidationError(
            field_name,
            "contains invalid characters (CRLF injection attempt)"
        )

    return value


def _interpolate_field(
    value: Optional[str],
    interp: Callable[[str], str],
) -> Optional[str]:
    """Interpolate a single optional string field.

    Args:
        value: The string value to interpolate (may be None).
        interp: Interpolation function (str → str).

    Returns:
        Interpolated value or None if input was None.

    Raises:
        TypeError: If interp is not callable.
    """
    if not callable(interp):
        raise TypeError(f"interp must be callable, got {type(interp)}")

    return interp(value) if value else None


class AuthStrategy(ABC):
    """Base class for authentication strategies.

    Subclasses must set two class-level attributes:

    - ``AUTH_TYPE: str``     — short key used in ``to_dict()["type"]``.
                               MUST be globally unique.
    - ``DISPLAY_NAME: str``  — human-readable label (e.g. ``"OAuth 2.0"``).

    Subclasses may override:
    - ``interpolate()`` — for custom interpolation logic.
    - ``interpolate_fields()`` — for optimized field-by-field interpolation.
    - ``get_display_summary()`` — for richer GUI display.
    - ``get_preflight_warning()`` — for validation warnings.
    """

    AUTH_TYPE: str = ""           # e.g. "bearer", "basic", "oauth2"
    DISPLAY_NAME: str = ""        # e.g. "Bearer Token", "OAuth 2.0"

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Validate that subclass defines AUTH_TYPE and DISPLAY_NAME.

        This catches missing required attributes at class definition time
        rather than at runtime, preventing cryptic errors later.
        """
        super().__init_subclass__(**kwargs)

        # Don't validate on intermediate abstract classes
        if not hasattr(cls, '__abstractmethods__') or not cls.__abstractmethods__:
            if not cls.AUTH_TYPE:
                raise TypeError(
                    f"{cls.__name__} must define AUTH_TYPE class variable"
                )
            if not cls.DISPLAY_NAME:
                raise TypeError(
                    f"{cls.__name__} must define DISPLAY_NAME class variable"
                )
            logger.debug("Registered auth strategy: %s (type=%s)",
                        cls.__name__, cls.AUTH_TYPE)

    # ── Core interface (must override) ────────────────────────────────

    @abstractmethod
    def apply(self, request: Any, headers: Dict[str, str]) -> None:
        """Apply authentication to request headers (and optionally params).

        Args:
            request: Request object (may be read for URL, params, body).
            headers: Headers dict to modify in-place.

        Raises:
            AuthError: If authentication cannot be applied.
        """

    @abstractmethod
    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a plain dict suitable for JSON / DB storage.

        The returned dict MUST include a "type" key matching AUTH_TYPE.

        Returns:
            Dictionary representation of this auth strategy.
        """

    @classmethod
    @abstractmethod
    def from_dict(cls, data: Dict[str, Any], **kwargs: Any) -> "AuthStrategy":
        """Reconstruct from a serialised dict.

        Base implementation provides validation of input data structure.
        Subclasses must call super().from_dict() or validate themselves.

        Args:
            data: Dictionary with at least "type" key.
            **kwargs: Additional options (subclass-specific).

        Returns:
            Reconstructed AuthStrategy instance.

        Raises:
            ValueError: If data is invalid or missing required keys.
            TypeError: If data is not a dictionary.
        """
        if not isinstance(data, dict):
            raise TypeError(f"Expected dict, got {type(data).__name__}")

        if "type" not in data:
            raise ValueError("Missing required 'type' key in auth data")

    # ── Optional overrides ────────────────────────────────────────────

    def interpolate(
        self,
        interp: Callable[[str], str],
    ) -> "AuthStrategy":
        """Return a *new* instance with ``{{VAR}}`` placeholders expanded.

        This method uses the template method pattern:
        1. Validates that interp is callable
        2. Calls ``interpolate_fields()`` for field-level customization
        3. Falls back to ``to_dict()``/``from_dict()`` round-trip if not overridden

        Override ``interpolate_fields()`` in subclasses for optimized
        interpolation of non-string state (e.g., timestamps).

        Args:
            interp: ``str → str`` variable-expansion function.

        Returns:
            New AuthStrategy instance with placeholders expanded.

        Raises:
            TypeError: If interp is not callable.
        """
        if not callable(interp):
            raise TypeError(f"interp must be callable, got {type(interp).__name__}")

        return self.interpolate_fields(interp)

    def interpolate_fields(
        self,
        interp: Callable[[str], str],
    ) -> "AuthStrategy":
        """Interpolate fields in this auth strategy.

        Default implementation round-trips through ``to_dict()``/``from_dict()``,
        interpolating all string values.

        Override in subclasses for optimized interpolation that preserves
        non-string state (e.g., expires_at timestamps, refresh tokens).

        Args:
            interp: ``str → str`` variable-expansion function.

        Returns:
            New AuthStrategy instance with placeholders expanded.
        """
        d = self.to_dict()
        interpolated = {
            k: (interp(v) if isinstance(v, str) and v else v)
            for k, v in d.items()
        }
        return type(self).from_dict(interpolated)

    def get_display_summary(self) -> str:
        """Return a short, non-secret summary string for GUI labels.

        Override in subclasses to provide richer detail (e.g., username for
        BasicAuth, "OAuth 2.0 (authenticated)" for OAuth2Auth).

        Returns:
            Human-readable summary string.
        """
        return self.DISPLAY_NAME or type(self).__name__

    def get_preflight_warning(self) -> Optional[str]:
        """Return an advisory warning if required fields are missing.

        Returns ``None`` when the configuration looks complete.

        Override in subclasses to validate required fields (e.g., check that
        token is set, credentials are configured).

        Returns:
            Warning message or None.
        """
        return None

    def __eq__(self, other: Any) -> bool:
        """Compare auth strategies by their serialized form.

        Two auth objects are equal if their to_dict() representations are equal.
        This allows comparing auth objects and using them in collections.

        Args:
            other: Other object to compare with.

        Returns:
            True if both are same auth type with same configuration.
        """
        if not isinstance(other, AuthStrategy):
            return NotImplemented

        try:
            return self.to_dict() == other.to_dict()
        except Exception:
            # If serialization fails, fall back to identity comparison
            logger.debug("Failed to compare auth objects by value", exc_info=True)
            return self is other

    def __repr__(self) -> str:
        """Return developer-friendly representation.

        Format: ClassName(type=auth_type, ...)
        Sensitive fields (password, token, secret, key) are redacted for safety.

        Returns:
            String representation for debugging.
        """
        try:
            data = self.to_dict()
            # Redact sensitive fields for safe logging
            safe_data = {
                k: "[REDACTED]" if any(sensitive in k.lower()
                                      for sensitive in ("password", "token", "secret", "key"))
                else v
                for k, v in data.items()
            }
            items = ", ".join(f"{k}={v!r}" for k, v in safe_data.items())
            return f"{type(self).__name__}({items})"
        except Exception:
            return f"{type(self).__name__}(error in __repr__)"

