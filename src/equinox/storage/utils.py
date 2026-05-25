"""Shared validation utilities for storage managers."""

import json
import logging
from typing import Any

from equinox.core.exceptions import SecurityError, ValidationError

logger = logging.getLogger(__name__)


# ── Shared storage limits ─────────────────────────────────────────────────
# Single source of truth — import these in every manager instead of
# redeclaring identical constants on each class.

MAX_NAME_LENGTH: int = 200
MAX_DESCRIPTION_LENGTH: int = 1_000
MAX_VARIABLE_KEY_LENGTH: int = 100
MAX_VARIABLE_VALUE_LENGTH: int = 10_000

# Private aliases kept for backward compatibility with older imports.
_MAX_VARIABLE_KEY_LENGTH = MAX_VARIABLE_KEY_LENGTH
_MAX_VARIABLE_VALUE_LENGTH = MAX_VARIABLE_VALUE_LENGTH

# Sentinel used by safe_json_loads to distinguish "no default given" from
# "caller explicitly passed None as the desired fallback value".
_MISSING: object = object()


def require_positive_int(value: Any, label: str) -> None:
    """Raise ValidationError unless *value* is a positive integer.

    Args:
        value: The value to check.
        label: Human-readable name for error messages
               (e.g. ``"Collection ID"``).
    """
    if not isinstance(value, int) or value <= 0:
        raise ValidationError(f"{label} must be a positive integer")


def validate_variable_key(key: Any, max_length: int = MAX_VARIABLE_KEY_LENGTH) -> str:
    """Validate and strip a variable key.  Returns the stripped key.

    Strips leading/trailing whitespace before checking length so that the
    meaningful content length is enforced, not the padded length.

    Raises:
        ValidationError: If *key* is not a non-empty string or is too long.
    """
    if not isinstance(key, str):
        raise ValidationError("Variable key must be a non-empty string")
    normalized_key = key.strip()
    if not normalized_key:
        raise ValidationError("Variable key cannot be empty or whitespace")
    if len(normalized_key) > max_length:
        raise ValidationError(f"Variable key too long (max {max_length} characters)")
    return normalized_key


def validate_variable_value(value: Any, max_length: int = MAX_VARIABLE_VALUE_LENGTH) -> str:
    """Validate a variable value and return it unchanged.

    Values are *not* stripped — leading/trailing whitespace is intentional
    (e.g. passwords, tokens).

    Raises:
        ValidationError: If *value* is not a string or is too long.
    """
    if not isinstance(value, str):
        raise ValidationError("Variable value must be a string")
    if len(value) > max_length:
        raise ValidationError(f"Variable value too long (max {max_length} characters)")
    return value


def require_str(value: Any, field: str, max_len: int, required: bool = True) -> str:
    """Validate and strip a string field.  Returns the stripped value.

    Args:
        value: The value to validate (``None`` is coerced to ``""``).
        field: Human-readable field name for error messages.
        max_len: Maximum allowed length (applied *after* stripping).
        required: If True, raise on empty/whitespace-only strings.

    Raises:
        ValidationError: If validation fails.
    """
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ValidationError(f"'{field}' must be a string")
    text_value = value
    if required and not text_value:
        raise ValidationError(f"'{field}' must be a non-empty string")
    normalized_value = text_value.strip()
    if required and not normalized_value:
        raise ValidationError(f"'{field}' cannot be empty or whitespace")
    if len(normalized_value) > max_len:
        raise ValidationError(f"'{field}' is too long (max {max_len} chars)")
    return normalized_value


# ── JSON helpers ───────────────────────────────────────────────────────────


def coerce_body_to_str(body: Any, strict: bool = False) -> str | None:
    """Coerce bytes/str/other body to a string suitable for indexing and matching.

    Returns None when body is None. If strict=True then decoding errors raise;
    otherwise decoding errors return an empty string.
    """
    if body is None:
        return None

    if isinstance(body, (bytes, bytearray)):
        try:
            return body.decode("utf-8", errors="strict" if strict else "replace")
        except Exception:
            if strict:
                raise
            return ""

    if isinstance(body, str):
        return body

    try:
        return str(body)
    except Exception:
        return ""


def safe_json_dumps(
    obj: Any,
    *,
    max_len: int | None = None,
    indent: int | None = None,
    ensure_ascii: bool = True,
    sort_keys: bool = False,
) -> str:
    """Serialize *obj* to a JSON string with optional safety limits.

    Args:
        obj: Object to serialize.
        max_len: If set, raise :class:`~equinox.core.exceptions.SecurityError`
            when the resulting string exceeds this length.
        indent: JSON indentation level (``None`` for compact output).
        ensure_ascii: Escape non-ASCII characters (default ``True``).
        sort_keys: Sort dictionary keys in the output.

    Returns:
        JSON string.

    Raises:
        SecurityError: If *max_len* is set and the output exceeds it.
    """
    s = json.dumps(obj, indent=indent, ensure_ascii=ensure_ascii, sort_keys=sort_keys)
    if max_len is not None and len(s) > max_len:
        raise SecurityError(f"JSON serialization exceeds {max_len} bytes")
    return s


def safe_json_loads(
    s: str | None,
    *,
    default: Any = _MISSING,
    row_id: int | None = None,
) -> Any:
    """Safely parse a JSON string, returning *default* on failure.

    Args:
        s: JSON string to parse (``None`` and empty strings return *default*).
        default: Value to return when *s* is empty/None or cannot be parsed.
            When omitted, ``{}`` is used.  Pass ``default=[]``, ``default=None``,
            etc. to override for specific column types.
        row_id: If provided, parse errors are logged at ERROR level with this
            context; otherwise they are logged at DEBUG level.

    Returns:
        Parsed JSON value, or *default* on failure.
    """
    resolved = {} if default is _MISSING else default
    if not s:
        return resolved
    try:
        return json.loads(s)
    except (json.JSONDecodeError, TypeError) as exc:
        if row_id is not None:
            logger.error("Failed to parse JSON for row %s: %s", row_id, exc)
        else:
            logger.debug("Failed to parse JSON: %s", exc)
        return resolved
