"""Variable interpolation for requests.

This module provides variable interpolation for {{variable}} placeholders in request
strings. It supports:
- URL, headers, params, body, name, and description fields
- Chained variable references (e.g., {{base}} can reference {{scheme}})
- Multi-pass interpolation with cycle detection
- Security limits to prevent DoS attacks

Example:
    >>> variables = {"host": "example.com", "scheme": "https"}
    >>> text = "{{scheme}}://{{host}}/api"
    >>> VariableInterpolator.interpolate(text, variables)
    'https://example.com/api'

Security:
    - Text size limited to 1 MB
    - Expansion ratio limited to 200x original length
    - Variable names must match pattern [a-zA-Z0-9_-]+
    - Circular references detected and warned
    - Unresolvable placeholders left unchanged
"""

import calendar
import copy
import logging
import os
import re
from collections.abc import Callable
from dataclasses import is_dataclass, replace as dataclass_replace
from datetime import date, datetime
from typing import Any, Optional, Tuple, TypeVar, cast

from equinox.core.exceptions import SecurityError, ValidationError

logger = logging.getLogger(__name__)

# Generic type variable for request objects
T = TypeVar("T")

# ──────────────────────────────────────────────────────────────────────────────
# Module constants
# ──────────────────────────────────────────────────────────────────────────────

# Encoding used for byte-length calculations and UTF-8 validation.
_TEXT_ENCODING: str = "utf-8"

# Pattern for valid variable names — shared by key validation and OS env filtering.
# Matches names that can appear inside {{...}} placeholders.
_VARIABLE_NAME_RE: re.Pattern = re.compile(r"^[a-zA-Z0-9_-]+$")


def _shift_months(base: date, delta_months: int) -> date:
    """Shift a date by whole months, clamping day to month length."""
    month0 = (base.month - 1) + delta_months
    year = base.year + (month0 // 12)
    month = (month0 % 12) + 1
    day = min(base.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _magic_variables(
    today: Optional[date] = None, now: Optional[datetime] = None
) -> dict[str, str]:
    """Return built-in dynamic variables for date/time convenience."""
    now_value = now or datetime.now()
    today_value = today or now_value.date()
    one_month_ago = _shift_months(today_value, -1)
    one_year_ago = _shift_months(today_value, -12)
    return {
        "TODAY": today_value.isoformat(),
        "ONE_MONTH_AGO": one_month_ago.isoformat(),
        "ONE_YEAR_AGO": one_year_ago.isoformat(),
        "NOW_ISO": now_value.isoformat(timespec="seconds"),
    }


def _validate_interpolation_inputs(
    text: str, variables: dict[str, str], max_input_bytes: int
) -> None:
    """Validate input types and enforce max input size."""
    if not isinstance(text, str):
        raise ValidationError("Text must be a string")
    if not isinstance(variables, dict):
        raise ValidationError("Variables must be a dictionary")

    try:
        text_bytes = text.encode(_TEXT_ENCODING)
    except UnicodeEncodeError as exc:
        logger.error("Invalid text encoding in input: %s", exc)
        raise ValidationError(f"Invalid UTF-8 in input text: {exc}") from exc

    if len(text_bytes) > max_input_bytes:
        logger.warning(
            "Input text exceeds maximum input size: %d bytes > %d bytes max",
            len(text_bytes),
            max_input_bytes,
        )
        raise SecurityError(
            f"Input text too large ({len(text_bytes):,} bytes, max {max_input_bytes:,} bytes)"
        )


def _sanitize_variables(variables: dict[Any, Any]) -> dict[str, str]:
    """Keep only valid string variable names and values."""
    sanitized_variables: dict[str, str] = {}
    for key, value in variables.items():
        if not isinstance(key, str):
            logger.debug("Skipping variable with non-string key: %r", key)
            continue
        if not isinstance(value, str):
            logger.debug(
                "Skipping variable %r with non-string value type: %s",
                key,
                type(value).__name__,
            )
            continue
        if not _VARIABLE_NAME_RE.match(key):
            logger.debug("Skipping variable with invalid name: %r", key)
            continue
        sanitized_variables[key] = value
    return sanitized_variables


def _build_casefold_lookup(sanitized_variables: dict[str, str]) -> tuple[dict[str, str], set[str]]:
    """Build casefold lookup and track ambiguous folded keys."""
    casefold_map: dict[str, str] = {}
    ambiguous_casefolds = set()
    for key, value in sanitized_variables.items():
        folded = key.casefold()
        current = casefold_map.get(folded)
        if current is None:
            casefold_map[folded] = value
        elif current != value:
            ambiguous_casefolds.add(folded)
    return casefold_map, ambiguous_casefolds


def _check_expansion_limits(
    text: str,
    original_length: int,
    expansion_limit: int,
    max_output_bytes: int,
) -> None:
    """Enforce expansion ratio and absolute output size limits."""
    if len(text) > expansion_limit:
        expansion_ratio = len(text) / original_length if original_length > 0 else float("inf")
        logger.warning(
            "Variable interpolation expansion too large: "
            "ratio=%.2f, output=%d bytes (limit=%d bytes)",
            expansion_ratio,
            len(text),
            expansion_limit,
        )
        raise SecurityError(
            f"Variable interpolation caused excessive text expansion: "
            f"{len(text):,} bytes (limit {expansion_limit:,} bytes, ratio {expansion_ratio:.1f}x)"
        )

    text_encoded = text.encode(_TEXT_ENCODING)
    if len(text_encoded) > max_output_bytes:
        logger.warning(
            "Variable interpolation output exceeds maximum absolute size: %d bytes > %d bytes",
            len(text_encoded),
            max_output_bytes,
        )
        raise SecurityError(
            f"Variable interpolation output exceeds maximum size "
            f"({len(text_encoded)} bytes, max {max_output_bytes} bytes)"
        )


def _build_substitute_variable(
    sanitized_variables: dict[str, str],
    casefold_map: dict[str, str],
    ambiguous_casefolds: set[str],
) -> Callable[[re.Match[str]], str]:
    """Build replacement callable for regex substitution."""

    def substitute_variable(match: re.Match[str]) -> str:
        var_name = match.group(1)
        exact = sanitized_variables.get(var_name)
        if exact is not None:
            return exact

        folded = var_name.casefold()
        if folded in ambiguous_casefolds:
            return match.group(0)

        fallback = casefold_map.get(folded)
        return fallback if fallback is not None else str(match.group(0))

    return substitute_variable


def _interpolate_until_stable(
    text: str,
    pattern: re.Pattern[str],
    substitute_variable: Callable[[re.Match[str]], str],
    iteration_limit: int,
    expansion_limit: int,
    max_output_bytes: int,
) -> str:
    """Run multi-pass interpolation with convergence and security guards."""
    original_length = len(text)
    for iteration in range(iteration_limit):
        previous_text = text
        text = pattern.sub(substitute_variable, text)
        if text == previous_text:
            logger.debug(
                "Variable interpolation converged at iteration %d/%d",
                iteration + 1,
                iteration_limit,
            )
            return text

        _check_expansion_limits(text, original_length, expansion_limit, max_output_bytes)

    logger.warning(
        "Variable interpolation reached max iterations (%d) without full convergence. "
        "Check for circular variable references.",
        iteration_limit,
    )
    return text


def _copy_request_object(request: T) -> T:
    """Return a shallow copy while preserving dataclass behavior."""
    if is_dataclass(request) and not isinstance(request, type):
        return cast(T, dataclass_replace(cast(Any, request)))
    return copy.copy(request)


def _interpolate_dict_values(
    values: dict[Any, Any],
    variables: dict[str, str],
    interpolator: type[VariableInterpolator],
) -> dict[str, str]:
    """Interpolate all key/value pairs in a dict-like request field."""
    return {
        interpolator.interpolate(str(key), variables): interpolator.interpolate(
            str(value), variables
        )
        for key, value in values.items()
    }


def _interpolate_request_string_field(
    request: T,
    field_name: str,
    variables: dict[str, str],
    interpolator: type[VariableInterpolator],
) -> None:
    """Interpolate one string request field in-place when present."""
    if hasattr(request, field_name):
        current = getattr(request, field_name)
        if isinstance(current, str):
            setattr(request, field_name, interpolator.interpolate(current, variables))


class VariableInterpolator:
    """Interpolate {{variable}} placeholders in request strings."""

    VARIABLE_PATTERN = re.compile(r"\{\{([a-zA-Z0-9_-]+)}}")

    MAX_ITERATIONS = 10
    MAX_EXPANSION_RATIO = 200
    # Hard ceiling on raw input size (checked before any expansion work).
    MAX_INPUT_BYTES = 1 * 1024 * 1024  # 1 MB
    # Hard ceiling on output size after variable substitution.
    MAX_OUTPUT_BYTES = 2 * 1024 * 1024  # 2 MB

    @classmethod
    def interpolate(
        cls,
        text: str,
        variables: dict[str, str],
        max_iterations: Optional[int] = None,
    ) -> str:
        """Interpolate {{var}} placeholders with bounded multi-pass expansion."""
        _validate_interpolation_inputs(text, variables, cls.MAX_INPUT_BYTES)

        logger.debug(
            "VariableInterpolator.interpolate() started with %d variables",
            len(variables),
        )

        sanitized_variables = _sanitize_variables(variables)
        casefold_map, ambiguous_casefolds = _build_casefold_lookup(sanitized_variables)

        if not text:
            return text
        if not cls.find_variables(text):
            return text

        iteration_limit = max_iterations or cls.MAX_ITERATIONS
        expansion_limit = len(text) * cls.MAX_EXPANSION_RATIO
        substitute_variable = _build_substitute_variable(
            sanitized_variables,
            casefold_map,
            ambiguous_casefolds,
        )
        return _interpolate_until_stable(
            text,
            cls.VARIABLE_PATTERN,
            substitute_variable,
            iteration_limit,
            expansion_limit,
            cls.MAX_OUTPUT_BYTES,
        )

    @classmethod
    def interpolate_request(cls, request: T, variables: dict[str, str]) -> T:
        """Interpolate variables in a copy of the Request object.

        Returns a new Request with {{variable}} placeholders expanded.
        The original request is never modified.

        Interpolation is applied to all string fields:
        - url: Request URL with placeholders
        - headers: Header names and values
        - params: Query parameter names and values
        - body: Request body content
        - name: Request name
        - description: Request description

        Args:
            request: Request object to interpolate (original unchanged)
            variables: Dictionary of {{variable}} → value mappings

        Returns:
            New request object with same type as input, with variables replaced

        Raises:
            ValidationError: If variables are invalid
            SecurityError: If interpolation would cause DoS
        """
        request = _copy_request_object(request)

        if not variables:
            return request

        # Interpolate string fields that commonly contain placeholders
        _interpolate_request_string_field(request, "url", variables, cls)

        if hasattr(request, "headers") and isinstance(request.headers, dict):
            request.headers = _interpolate_dict_values(request.headers, variables, cls)

        if hasattr(request, "params") and isinstance(request.params, dict):
            request.params = _interpolate_dict_values(request.params, variables, cls)

        _interpolate_request_string_field(request, "body", variables, cls)
        _interpolate_request_string_field(request, "name", variables, cls)
        _interpolate_request_string_field(request, "description", variables, cls)

        return request

    @classmethod
    def find_variables(cls, text: object) -> list[str]:
        """Find all unique variable names referenced in text.

        Searches for all {{variable}} patterns and extracts the variable names.
        Variable names must match the pattern [a-zA-Z0-9_-]+.

        Args:
            text: Text to search for {{variable}} placeholders

        Returns:
            List of unique variable names found (empty list if none)

        Raises:
            ValidationError: If text is not a string
        """
        if not isinstance(text, str):
            # For API convenience, return empty list when input is not a string
            return []
        return list(set(cls.VARIABLE_PATTERN.findall(text)))

    @classmethod
    def has_variables(cls, text: object) -> bool:
        """Return True if text contains any {{variable}} placeholders.

        Quick check to see if interpolation is needed.

        Args:
            text: Text to check

        Returns:
            True if text contains at least one {{variable}} pattern

        Raises:
            ValidationError: If text is not a string
        """
        if not isinstance(text, str):
            # Non-string inputs are considered to have no variables
            return False
        return bool(cls.VARIABLE_PATTERN.search(text))


def _normalize_values(raw: dict[Any, Any], source: str) -> dict[str, str]:
    """Normalize variable dict values to strings while skipping invalid entries."""
    normalized: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            logger.debug(
                "collect_interpolation_variables: skipping %s var with non-string key: %r",
                source,
                key,
            )
            continue
        if value is None:
            logger.debug(
                "collect_interpolation_variables: skipping %s var %r with None value",
                source,
                key,
            )
            continue
        if isinstance(value, str):
            normalized[key] = value
            continue
        try:
            normalized[key] = str(value)
            logger.debug(
                "collect_interpolation_variables: coerced %s var %r from %s to string",
                source,
                key,
                type(value).__name__,
            )
        except Exception as exc:
            logger.debug(
                "collect_interpolation_variables: failed to coerce %s var %r (%s)",
                source,
                key,
                exc,
            )
    return normalized


def _merge_sourced_variables(
    variables: dict[str, str],
    sources: dict[str, str],
    incoming: dict[str, str],
    source_name: str,
) -> None:
    """Merge incoming values and tag each key with its source label."""
    variables.update(incoming)
    for key in incoming.keys():
        sources[str(key)] = source_name


def _merge_os_variables(variables: dict[str, str], sources: dict[str, str]) -> None:
    """Add OS env vars that are valid names and not already set."""
    os_vars = {
        key: value
        for key, value in os.environ.items()
        if isinstance(value, str) and _VARIABLE_NAME_RE.match(key)
    }

    os_inserted = 0
    os_skipped = 0
    for key, value in os_vars.items():
        if key in variables:
            os_skipped += 1
            continue
        variables[key] = value
        sources[key] = "os"
        os_inserted += 1

    logger.debug(
        "collect_interpolation_variables: %d OS env vars (%d inserted, %d skipped due to higher-priority vars)",
        len(os_vars),
        os_inserted,
        os_skipped,
    )


def _load_global_variables(db: Any) -> dict[str, str]:
    """Load global interpolation variables from storage."""
    try:
        from equinox.storage.global_variables import GlobalVariablesManager

        global_mgr = GlobalVariablesManager(db)
        global_vars = _normalize_values(global_mgr.get_variables_dict(), "global")
        logger.debug("collect_interpolation_variables: %d global vars", len(global_vars))
        return global_vars
    except Exception as exc:
        logger.warning("Failed to load global variables: %s", exc)
        return {}


def _load_environment_variables(db: Any) -> dict[str, str]:
    """Load active environment interpolation variables from storage."""
    try:
        from equinox.storage.environments import EnvironmentManager

        env_mgr = EnvironmentManager(db)
        active = env_mgr.get_active_environment()
        if active and isinstance(active.get("variables"), dict):
            env_vars = _normalize_values(active["variables"], "environment")
            logger.debug("collect_interpolation_variables: %d env vars", len(env_vars))
            return env_vars
    except Exception as exc:
        logger.warning("Failed to load active environment variables: %s", exc)
    return {}


def _load_collection_variables(db: Any, collection_id: Optional[int]) -> dict[str, str]:
    """Load collection-scoped interpolation variables from storage."""
    if collection_id is None:
        return {}

    try:
        from equinox.storage.collections import CollectionManager

        col_mgr = CollectionManager(db)
        raw_col_vars = col_mgr.get_all_collection_variables(collection_id)
        col_vars = _normalize_values(raw_col_vars, "collection")
        logger.debug(
            "collect_interpolation_variables: %d collection vars (coll=%d)",
            len(col_vars),
            collection_id,
        )
        return col_vars
    except Exception as exc:
        logger.warning(
            "Failed to load collection variables for collection %d: %s",
            collection_id,
            exc,
        )
        return {}


def collect_interpolation_variables_detailed(
    db: Any,
    collection_id: Optional[int] = None,
    session_vars: Optional[dict[str, str]] = None,
) -> Tuple[dict[str, str], dict[str, str]]:
    """Collect interpolation variables and source labels by precedence order."""

    variables: dict[str, str] = {}
    sources: dict[str, str] = {}

    builtin = _magic_variables()
    _merge_sourced_variables(variables, sources, builtin, "magic")
    logger.debug("collect_interpolation_variables: %d magic vars", len(builtin))

    _merge_sourced_variables(variables, sources, _load_global_variables(db), "global")
    _merge_sourced_variables(variables, sources, _load_environment_variables(db), "environment")
    _merge_sourced_variables(
        variables,
        sources,
        _load_collection_variables(db, collection_id),
        "collection",
    )

    _merge_os_variables(variables, sources)

    if session_vars:
        normalized_session = _normalize_values(session_vars, "session")
        _merge_sourced_variables(variables, sources, normalized_session, "session")
        logger.debug("collect_interpolation_variables: %d session vars", len(normalized_session))

    base_url_value = variables.get("BASE_URL")
    if isinstance(base_url_value, str):
        logger.debug(
            "collect_interpolation_variables: BASE_URL source=%s value_is_template=%s",
            sources.get("BASE_URL", "unknown"),
            bool(VariableInterpolator.has_variables(base_url_value)),
        )

    logger.debug("collect_interpolation_variables: %d total variables", len(variables))
    return variables, sources


def collect_interpolation_variables(
    db: Any,
    collection_id: Optional[int] = None,
    session_vars: Optional[dict[str, str]] = None,
) -> dict[str, str]:
    """Compatibility wrapper returning only collected variables."""
    variables, _sources = collect_interpolation_variables_detailed(
        db,
        collection_id=collection_id,
        session_vars=session_vars,
    )
    return variables
