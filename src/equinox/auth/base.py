"""Base authentication strategy.

Provides the ``AuthStrategy`` abstract base class and shared validation.

Every concrete strategy must define:

- ``AUTH_TYPE``      — short identifier (e.g. ``"bearer"``, ``"basic"``).
- ``DISPLAY_NAME``   — human-readable label for GUI rendering.
- ``apply()``        — inject credentials into request headers/params.
- ``to_dict()``      — round-trip serialisation.
- ``from_dict()``    — deserialisation from a dict.

Optional overrides for richer behaviour:

- ``interpolate()``  — return a copy with ``{{VAR}}`` placeholders expanded.
- ``get_display_summary()``  — one-liner summary for the GUI auth tab.
- ``get_preflight_warning()``  — advisory string when required fields are empty.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, Optional

from equinox.core.exceptions import AuthError

# Maximum length for any single credential value (tokens, passwords, keys).
_MAX_CREDENTIAL_LENGTH = 16_384


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
        AuthError: If validation fails.
    """
    if not isinstance(value, str) or not value:
        raise AuthError(f"{field_name} must be a non-empty string")
    if len(value) > _MAX_CREDENTIAL_LENGTH:
        raise AuthError(
            f"{field_name} exceeds maximum length ({_MAX_CREDENTIAL_LENGTH})"
        )
    if "\r" in value or "\n" in value:
        raise AuthError(
            f"{field_name} contains invalid characters (CRLF injection attempt)"
        )
    return value


def _interpolate_field(
    value: Optional[str],
    interp: Callable[[str], str],
) -> Optional[str]:
    """Interpolate a single optional string field.

    Returns ``None`` unchanged; non-empty strings are passed through *interp*.
    """
    return interp(value) if value else None


class AuthStrategy(ABC):
    """Base class for authentication strategies.

    Subclasses must set two class-level attributes:

    - ``AUTH_TYPE: str``     — short key used in ``to_dict()["type"]``.
    - ``DISPLAY_NAME: str``  — human-readable label (e.g. ``"OAuth 2.0"``).
    """

    AUTH_TYPE: str = ""           # e.g. "bearer", "basic", "oauth2"
    DISPLAY_NAME: str = ""        # e.g. "Bearer Token", "OAuth 2.0"

    # ── Core interface (must override) ────────────────────────────────

    @abstractmethod
    def apply(self, request: Any, headers: Dict[str, str]) -> None:
        """Apply authentication to request headers (and optionally params).

        Args:
            request: Request object (may be read for URL, params, body).
            headers: Headers dict to modify in-place.
        """

    @abstractmethod
    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a plain dict suitable for JSON / DB storage."""

    @classmethod
    @abstractmethod
    def from_dict(cls, data: Dict[str, Any], **kwargs: Any) -> "AuthStrategy":
        """Reconstruct from a serialised dict."""

    # ── Optional overrides ────────────────────────────────────────────

    def interpolate(
        self,
        interp: Callable[[str], str],
    ) -> "AuthStrategy":
        """Return a *new* instance with ``{{VAR}}`` placeholders expanded.

        The default implementation round-trips through ``to_dict`` /
        ``from_dict``, interpolating every string value.  Subclasses with
        non-string state (e.g. ``expires_at``) should override to preserve it.

        Args:
            interp: ``str → str`` variable-expansion function.
        """
        d = self.to_dict()
        interpolated = {
            k: (interp(v) if isinstance(v, str) and v else v)
            for k, v in d.items()
        }
        return type(self).from_dict(interpolated)

    def get_display_summary(self) -> str:
        """Return a short, non-secret summary string for GUI labels.

        Override in subclasses to provide richer detail.
        """
        return self.DISPLAY_NAME or type(self).__name__

    def get_preflight_warning(self) -> Optional[str]:
        """Return an advisory warning if required fields are missing.

        Returns ``None`` when the configuration looks complete.
        """
        return None
