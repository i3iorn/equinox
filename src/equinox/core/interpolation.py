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
from dataclasses import replace as dataclass_replace
from typing import Dict, Any, Optional, TypeVar, List

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
            return sanitized_variables.get(var_name, match.group(0))

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


def collect_interpolation_variables(
    db,
    collection_id: Optional[int] = None,
    session_vars: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    """Collect all variable sources into a single ordered dict.

    Resolution order (each layer overrides the previous):
    1. Active database environment variables
    2. Collection-level variables (if *collection_id* is given)
    3. OS environment variables with valid interpolation-safe names
    4. *session_vars* (script-captured values, highest precedence)

    This is the canonical implementation — both the CLI
    (:func:`equinox.cli.main.get_interpolation_variables`) and the GUI
    (:class:`~equinox.gui.request_panel.mixins._RequestSendMixin`) delegate
    to this function so the resolution order is never inconsistent.

    Args:
        db: Open :class:`~equinox.storage.database.Database` instance.
        collection_id: When provided, collection variables are included.
        session_vars: Script-captured session variables (optional).

    Returns:
        Dict mapping variable name → string value.
    """
    # Lazy imports — avoid hard-coupling storage into the core package at
    # import time; these modules may not be available in all test contexts.
    from equinox.storage.environments import EnvironmentManager

    variables: Dict[str, str] = {}

    # 1. Active environment
    try:
        env_mgr = EnvironmentManager(db)
        active = env_mgr.get_active_environment()
        if active and isinstance(active.get("variables"), dict):
            variables.update(active["variables"])
            logger.debug("collect_interpolation_variables: %d env vars", len(active["variables"]))
    except Exception as exc:
        logger.warning("Failed to load active environment variables: %s", exc)

    # 2. Collection variables
    if collection_id is not None:
        try:
            from equinox.storage.collections import CollectionManager
            col_mgr = CollectionManager(db)
            col_vars = col_mgr.get_all_collection_variables(collection_id)
            variables.update(col_vars)
            logger.debug(
                "collect_interpolation_variables: %d collection vars (coll=%d)",
                len(col_vars), collection_id,
            )
        except Exception as exc:
            logger.warning(
                "Failed to load collection variables for collection %d: %s",
                collection_id, exc,
            )

    # 3. OS environment variables — only names safe for {{VAR}} interpolation
    os_vars = {k: v for k, v in os.environ.items() if isinstance(v, str) and _VARIABLE_NAME_RE.match(k)}
    variables.update(os_vars)
    logger.debug("collect_interpolation_variables: %d OS env vars", len(os_vars))

    # 4. Session variables (highest precedence — script output)
    if session_vars:
        variables.update(session_vars)
        logger.debug("collect_interpolation_variables: %d session vars", len(session_vars))

    logger.debug("collect_interpolation_variables: %d total variables", len(variables))
    return variables

