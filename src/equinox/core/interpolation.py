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

import copy
import re
import logging
import os
import calendar
from datetime import date, datetime
from dataclasses import replace as dataclass_replace
from typing import Dict, Any, Optional, TypeVar, List, Tuple

from equinox.core.exceptions import ValidationError, SecurityError

logger = logging.getLogger(__name__)

# Generic type variable for request objects
T = TypeVar('T')

# ──────────────────────────────────────────────────────────────────────────────
# Module constants
# ──────────────────────────────────────────────────────────────────────────────

# Encoding used for byte-length calculations and UTF-8 validation.
_TEXT_ENCODING: str = "utf-8"

# Pattern for valid variable names — shared by key validation and OS env filtering.
# Matches names that can appear inside {{...}} placeholders.
_VARIABLE_NAME_RE: re.Pattern = re.compile(r'^[a-zA-Z0-9_-]+$')


def _shift_months(base: date, delta_months: int) -> date:
    """Shift a date by whole months, clamping day to month length."""
    month0 = (base.month - 1) + delta_months
    year = base.year + (month0 // 12)
    month = (month0 % 12) + 1
    day = min(base.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _magic_variables(today: Optional[date] = None, now: Optional[datetime] = None) -> Dict[str, str]:
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


class VariableInterpolator:
    """Interpolate {{variable}} placeholders in request strings."""

    VARIABLE_PATTERN = re.compile(r'\{\{([a-zA-Z0-9_-]+)\}\}')

    MAX_ITERATIONS = 10
    MAX_EXPANSION_RATIO = 200
    # Hard ceiling on raw input size (checked before any expansion work).
    MAX_INPUT_BYTES = 1 * 1024 * 1024    # 1 MB
    # Hard ceiling on output size after variable substitution.
    MAX_OUTPUT_BYTES = 2 * 1024 * 1024   # 2 MB


    @classmethod
    def interpolate(cls, text: str, variables: Dict[str, str], max_iterations: Optional[int] = None) -> str:
        """Interpolate variables in text.

        Performs multiple passes so that chained references like
        {{base}} → {{scheme}}://host resolve fully.
        Unresolvable placeholders are left unchanged.

        Args:
            text: Text containing {{variable}} placeholders
            variables: Dictionary of variable values (keys must match [a-zA-Z0-9_-]+)
            max_iterations: Maximum interpolation passes to prevent infinite loops (default: 10)

        Returns:
            Text with {{variable}} placeholders replaced

        Raises:
            ValidationError: If text or variables are invalid
            SecurityError: If interpolation would cause excessive expansion or circular reference
        """
        # ── Input validation ──────────────────────────────────────────────────
        if not isinstance(text, str):
            raise ValidationError("Text must be a string")

        if not isinstance(variables, dict):
            raise ValidationError("Variables must be a dictionary")
        
        logger.debug(
            "VariableInterpolator.interpolate() started with %d variables",
            len(variables),
        )

        # Validate input size upfront (early exit for large inputs)
        try:
            text_bytes = text.encode(_TEXT_ENCODING)
            if len(text_bytes) > cls.MAX_INPUT_BYTES:
                logger.warning(
                    "Input text exceeds maximum input size: %d bytes > %d bytes max",
                    len(text_bytes), cls.MAX_INPUT_BYTES,
                )
                raise SecurityError(
                    f"Input text too large ({len(text_bytes):,} bytes, "
                    f"max {cls.MAX_INPUT_BYTES:,} bytes)"
                )
        except UnicodeEncodeError as e:
            # str.encode() raises UnicodeEncodeError for lone surrogates and
            # other characters that cannot be represented in the target encoding.
            logger.error("Invalid text encoding in input: %s", e)
            raise ValidationError(f"Invalid UTF-8 in input text: {e}") from e

        # Sanitize variables: only keep string keys/values with valid names.
        sanitized_variables: Dict[str, str] = {}
        for key, value in variables.items():
            if not isinstance(key, str):
                logger.debug("Skipping variable with non-string key: %r", key)
                continue
            if not isinstance(value, str):
                logger.debug("Skipping variable %r with non-string value type: %s", key, type(value).__name__)
                continue
            if not _VARIABLE_NAME_RE.match(key):
                logger.debug("Skipping variable with invalid name: %r", key)
                continue
            sanitized_variables[key] = value

        # Build a case-insensitive fallback map so {{BASE_URL}} and {{base_url}}
        # can resolve to the same variable when only one casing is defined.
        # If multiple keys differ only by case and have conflicting values,
        # treat the folded key as ambiguous and leave placeholders unchanged.
        casefold_map: Dict[str, str] = {}
        ambiguous_casefolds = set()
        for key, value in sanitized_variables.items():
            folded = key.casefold()
            current = casefold_map.get(folded)
            if current is None:
                casefold_map[folded] = value
            elif current != value:
                ambiguous_casefolds.add(folded)

        # ── Early exit cases ──────────────────────────────────────────────────
        if not text:
            return text

        # Skip interpolation entirely when there are no placeholders.
        if not cls.find_variables(text):
            return text

        # ── Interpolation loop ──────────────────────────────────────────────────
        iteration_limit = max_iterations or cls.MAX_ITERATIONS
        original_length = len(text)
        # Ratio-based expansion budget.  MAX_EXPANSION_RATIO=200 is generous
        # enough for realistic large values (e.g. a JWT from {{ACCESS_TOKEN}}
        # expands ~149x) while still catching expansion bombs in one or two
        # substitution passes.  MAX_OUTPUT_BYTES is the hard absolute ceiling.
        expansion_limit = original_length * cls.MAX_EXPANSION_RATIO

        def substitute_variable(match: re.Match) -> str:
            """Replace matched {{variable}} with its value or leave unchanged."""
            var_name = match.group(1)
            exact = sanitized_variables.get(var_name)
            if exact is not None:
                return exact

            folded = var_name.casefold()
            if folded in ambiguous_casefolds:
                return match.group(0)

            fallback = casefold_map.get(folded)
            return fallback if fallback is not None else match.group(0)

        for iteration in range(iteration_limit):
            previous_text = text
            text = cls.VARIABLE_PATTERN.sub(substitute_variable, text)

            # If no changes, interpolation complete
            if text == previous_text:
                logger.debug(
                    "Variable interpolation converged at iteration %d/%d",
                    iteration + 1, iteration_limit,
                )
                return text

            # Prevent expansion attacks
            if len(text) > expansion_limit:
                expansion_ratio = len(text) / original_length if original_length > 0 else float("inf")
                logger.warning(
                    "Variable interpolation expansion too large: "
                    "ratio=%.2f, output=%d bytes (limit=%d bytes)",
                    expansion_ratio, len(text), expansion_limit,
                )
                raise SecurityError(
                    f"Variable interpolation caused excessive text expansion: "
                    f"{len(text):,} bytes (limit {expansion_limit:,} bytes, "
                    f"ratio {expansion_ratio:.1f}x)"
                )

            # Double-check absolute size limit — encode once and reuse.
            text_encoded = text.encode(_TEXT_ENCODING)
            if len(text_encoded) > cls.MAX_OUTPUT_BYTES:
                logger.warning(
                    "Variable interpolation output exceeds maximum absolute size: %d bytes > %d bytes",
                    len(text_encoded), cls.MAX_OUTPUT_BYTES,
                )
                raise SecurityError(
                    f"Variable interpolation output exceeds maximum size "
                    f"({len(text_encoded)} bytes, max {cls.MAX_OUTPUT_BYTES} bytes)"
                )

        # Reached max iterations without convergence
        logger.warning(
            "Variable interpolation reached max iterations (%d) without full convergence. "
            "Check for circular variable references.",
            iteration_limit,
        )

        return text

    @classmethod
    def interpolate_request(cls, request: T, variables: Dict[str, str]) -> T:
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
        # Shallow-copy the request; use dataclasses.replace for dataclasses,
        # fall back to copy.copy for anything else.
        try:
            request = dataclass_replace(request)
        except TypeError:
            request = copy.copy(request)

        if not variables:
            return request

        # Interpolate string fields that commonly contain placeholders
        if hasattr(request, 'url') and isinstance(request.url, str):
            request.url = cls.interpolate(request.url, variables)

        if hasattr(request, 'headers') and isinstance(request.headers, dict):
            request.headers = {
                cls.interpolate(str(key), variables): cls.interpolate(str(value), variables)
                for key, value in request.headers.items()
            }

        if hasattr(request, 'params') and isinstance(request.params, dict):
            request.params = {
                cls.interpolate(str(key), variables): cls.interpolate(str(value), variables)
                for key, value in request.params.items()
            }

        if hasattr(request, 'body') and isinstance(request.body, str):
            request.body = cls.interpolate(request.body, variables)

        if hasattr(request, 'name') and isinstance(request.name, str):
            request.name = cls.interpolate(request.name, variables)

        if hasattr(request, 'description') and isinstance(request.description, str):
            request.description = cls.interpolate(request.description, variables)

        return request

    @classmethod
    def find_variables(cls, text: str) -> List[str]:
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
    def has_variables(cls, text: str) -> bool:
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


def collect_interpolation_variables_detailed(
    db,
    collection_id: Optional[int] = None,
    session_vars: Optional[Dict[str, str]] = None,
) -> Tuple[Dict[str, str], Dict[str, str]]:
    """Collect interpolation vars and their source labels.

    Resolution order (later layers only override where noted):
    1. Active database environment variables
    2. Collection-level variables (if *collection_id* is given)
    3. OS environment variables (fill only; no overwrite of DB-scoped vars)
    4. *session_vars* (highest precedence)
    """
    from equinox.storage.environments import EnvironmentManager

    def _normalize_values(raw: Dict[str, Any], source: str) -> Dict[str, str]:
        normalized: Dict[str, str] = {}
        for key, value in raw.items():
            if not isinstance(key, str):
                logger.debug(
                    "collect_interpolation_variables: skipping %s var with non-string key: %r",
                    source, key,
                )
                continue
            if value is None:
                logger.debug(
                    "collect_interpolation_variables: skipping %s var %r with None value",
                    source, key,
                )
                continue
            if isinstance(value, str):
                normalized[key] = value
                continue
            try:
                normalized[key] = str(value)
                logger.debug(
                    "collect_interpolation_variables: coerced %s var %r from %s to string",
                    source, key, type(value).__name__,
                )
            except Exception as exc:
                logger.debug(
                    "collect_interpolation_variables: failed to coerce %s var %r (%s)",
                    source, key, exc,
                )
        return normalized

    variables: Dict[str, str] = {}
    sources: Dict[str, str] = {}

    builtin = _magic_variables()
    variables.update(builtin)
    for key in builtin.keys():
        sources[key] = "magic"
    logger.debug("collect_interpolation_variables: %d magic vars", len(builtin))

    try:
        from equinox.storage.global_variables import GlobalVariablesManager
        global_mgr = GlobalVariablesManager(db)
        global_vars = _normalize_values(global_mgr.get_variables_dict(), "global")
        variables.update(global_vars)
        for key in global_vars.keys():
            sources[str(key)] = "global"
        logger.debug("collect_interpolation_variables: %d global vars", len(global_vars))
    except Exception as exc:
        logger.warning("Failed to load global variables: %s", exc)

    try:
        env_mgr = EnvironmentManager(db)
        active = env_mgr.get_active_environment()
        if active and isinstance(active.get("variables"), dict):
            env_vars = _normalize_values(active["variables"], "environment")
            variables.update(env_vars)
            for key in env_vars.keys():
                sources[str(key)] = "environment"
            logger.debug("collect_interpolation_variables: %d env vars", len(env_vars))
    except Exception as exc:
        logger.warning("Failed to load active environment variables: %s", exc)

    if collection_id is not None:
        try:
            from equinox.storage.collections import CollectionManager
            col_mgr = CollectionManager(db)
            raw_col_vars = col_mgr.get_all_collection_variables(collection_id)
            col_vars = _normalize_values(raw_col_vars, "collection")
            variables.update(col_vars)
            for key in col_vars.keys():
                sources[str(key)] = "collection"
            logger.debug(
                "collect_interpolation_variables: %d collection vars (coll=%d)",
                len(col_vars), collection_id,
            )
        except Exception as exc:
            logger.warning(
                "Failed to load collection variables for collection %d: %s",
                collection_id, exc,
            )

    os_vars = {k: v for k, v in os.environ.items() if isinstance(v, str) and _VARIABLE_NAME_RE.match(k)}
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
        len(os_vars), os_inserted, os_skipped,
    )

    if session_vars:
        normalized_session = _normalize_values(session_vars, "session")
        variables.update(normalized_session)
        for key in normalized_session.keys():
            sources[str(key)] = "session"
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
    db,
    collection_id: Optional[int] = None,
    session_vars: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    """Compatibility wrapper returning only collected variables."""
    variables, _sources = collect_interpolation_variables_detailed(
        db,
        collection_id=collection_id,
        session_vars=session_vars,
    )
    return variables

