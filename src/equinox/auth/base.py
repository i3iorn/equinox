"""Base authentication strategy"""

from abc import ABC, abstractmethod
from typing import Dict, Any

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


class AuthStrategy(ABC):
    """Base class for authentication strategies"""

    @abstractmethod
    def apply(self, request: Any, headers: Dict[str, str]) -> None:
        """
        Apply authentication to request headers

        Args:
            request: Request object
            headers: Headers dictionary to modify
        """
        pass

    @abstractmethod
    def to_dict(self) -> Dict[str, Any]:
        """Convert auth strategy to dictionary for storage"""
        pass

    @classmethod
    @abstractmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AuthStrategy":
        """Create auth strategy from dictionary"""
        pass
