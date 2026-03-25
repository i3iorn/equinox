"""Shared validation utilities for storage managers."""

import json
from typing import Any, Optional, Dict

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


def require_str(value, field: str, max_len: int, required: bool = True) -> str:
    """Validate and strip a string field.  Returns the stripped value.

    Args:
        value: The value to validate (will be coerced from None to "").
        field: Human-readable field name for error messages.
        max_len: Maximum allowed length.
        required: If True, raise on empty/whitespace-only strings.

    Raises:
        ValidationError: If validation fails.
    """
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ValidationError(f"'{field}' must be a string")
    value = value.strip()
    if required and not value:
        raise ValidationError(f"'{field}' is required")
    if len(value) > max_len:
        raise ValidationError(f"'{field}' is too long (max {max_len} chars)")
    return value


# Additional helpers used by storage modules for consistent JSON and body handling
def _coerce_body_to_str(body: Any, strict: bool = False) -> Optional[str]:
    """Coerce bytes/str/other body to a string suitable for indexing and matching.

    Returns None when body is None. If strict=True then decoding errors raise;
    otherwise decoding errors return an empty string.
    """
    if body is None:
        return None

    if isinstance(body, (bytes, bytearray)):
        try:
            return bytes(body).decode("utf-8", errors="strict" if strict else "replace")
        except Exception:
            if strict:
                raise
            return ""

    if isinstance(body, str):
        return body

    # Coerce other types to string
    try:
        return str(body)
    except Exception:
        return ""


def _safe_json_dumps(obj: Any, *, max_len: int) -> str:
    """Serialize to JSON and enforce a maximum byte-length.

    Raises SecurityError if the resulting JSON exceeds max_len.
    """
    s = json.dumps(obj)
    if len(s) > max_len:
        from equinox.core.exceptions import SecurityError as _SE

        raise _SE(f"JSON serialization exceeds {max_len} bytes")
    return s


def _safe_json_loads(s: Optional[str], *, row_id: Optional[int] = None) -> Dict[str, Any]:
    """Safely parse JSON string to dict. Logs parsing errors and returns {} on failure.

    If row_id is provided, the parse error is logged with context.
    """
    if not s:
        return {}
    try:
        return json.loads(s)
    except (json.JSONDecodeError, TypeError) as exc:
        import logging

        logger = logging.getLogger(__name__)
        if row_id is not None:
            logger.error("Failed to parse JSON for history %s: %s", row_id, exc)
        else:
            logger.debug("Failed to parse JSON: %s", exc)
        return {}


# Public aliases (preferred by external modules). Keep the internal
# underscore-prefixed names for backwards compatibility with older imports.
def coerce_body_to_str(body: Any, strict: bool = False) -> Optional[str]:
    return _coerce_body_to_str(body, strict=strict)


def safe_json_dumps(obj: Any, *, max_len: int) -> str:
    return _safe_json_dumps(obj, max_len=max_len)


def safe_json_loads(s: Optional[str], *, row_id: Optional[int] = None) -> Dict[str, Any]:
    return _safe_json_loads(s, row_id=row_id)


