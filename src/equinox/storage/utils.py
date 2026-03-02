"""Shared validation utilities for storage managers."""

from equinox.core.exceptions import ValidationError


def require_positive_int(value, label: str) -> None:
    """Raise ValidationError unless *value* is a positive integer.

    Args:
        value: The value to check.
        label: Human-readable name for error messages
               (e.g. ``"Collection ID"``).
    """
    if not isinstance(value, int) or value <= 0:
        raise ValidationError(f"{label} must be a positive integer")


_MAX_VARIABLE_KEY_LENGTH = 100
_MAX_VARIABLE_VALUE_LENGTH = 10_000


def validate_variable_key(key, max_length: int = _MAX_VARIABLE_KEY_LENGTH) -> str:
    """Validate and strip a variable key.  Returns the stripped key.

    Raises:
        ValidationError: If *key* is not a non-empty string or is too long.
    """
    if not key or not isinstance(key, str):
        raise ValidationError("Variable key must be a non-empty string")
    if len(key) > max_length:
        raise ValidationError(
            f"Variable key too long (max {max_length} characters)"
        )
    key = key.strip()
    if not key:
        raise ValidationError("Variable key cannot be empty or whitespace")
    return key


def validate_variable_value(value, max_length: int = _MAX_VARIABLE_VALUE_LENGTH) -> None:
    """Validate a variable value.

    Raises:
        ValidationError: If *value* is not a string or is too long.
    """
    if not isinstance(value, str):
        raise ValidationError("Variable value must be a string")
    if len(value) > max_length:
        raise ValidationError(
            f"Variable value too long (max {max_length} characters)"
        )
