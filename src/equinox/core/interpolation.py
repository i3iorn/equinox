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
    - Text size limited to 1MB
    - Expansion ratio limited to 100x
    - Variable names must match pattern [a-zA-Z0-9_-]+
    - Circular references detected and warned
    - Unresolvable placeholders left unchanged
"""

import copy
import re
import logging
from typing import Dict, Any, Optional, TypeVar, Set, List
from dataclasses import replace as _dc_replace

from equinox.core.exceptions import ValidationError, SecurityError

logger = logging.getLogger(__name__)

# Generic type variable for request objects
T = TypeVar('T')


class VariableInterpolator:
    """Interpolate {{variable}} placeholders in request strings."""

    VARIABLE_PATTERN = re.compile(r'\{\{([a-zA-Z0-9_-]+)\}\}')

    MAX_ITERATIONS = 10
    MAX_EXPANSION_RATIO = 100
    MAX_OUTPUT_BYTES = 1 * 1024 * 1024  # 1 MB absolute ceiling

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
            text_bytes = text.encode("utf-8")
            if len(text_bytes) > cls.MAX_OUTPUT_BYTES:
                logger.warning(
                    "Input text exceeds maximum size: %d bytes > %d bytes max",
                    len(text_bytes), cls.MAX_OUTPUT_BYTES,
                )
                raise SecurityError(
                    f"Input text too large ({len(text_bytes)} bytes, "
                    f"max {cls.MAX_OUTPUT_BYTES} bytes)"
                )
        except UnicodeDecodeError as e:
            logger.error("Invalid UTF-8 in input text: %s", e)
            raise ValidationError(f"Invalid UTF-8 in input text: {e}")

        # Validate variable keys match expected pattern
        for key, value in variables.items():
            if not isinstance(key, str):
                raise ValidationError(f"Variable key must be string, got {type(key).__name__}")
            if not isinstance(value, str):
                raise ValidationError(f"Variable value for '{key}' must be string, got {type(value).__name__}")
            if not cls.VARIABLE_PATTERN.match(f"{{{{{key}}}}}"):
                raise ValidationError(
                    f"Invalid variable name '{key}'. "
                    f"Names must contain only alphanumeric characters, underscores, or hyphens."
                )

        # ── Early exit cases ──────────────────────────────────────────────────
        if not text:
            return text
        
        # Find needed variables (optimization: don't interpolate unused ones)
        needed_vars = cls.find_variables(text)
        if not needed_vars:
            return text  # No placeholders found
        
        # Filter to only variables actually referenced in text
        filtered_variables = {k: v for k, v in variables.items() if k in needed_vars}
        if not filtered_variables:
            return text  # No matching variables

        # ── Interpolation loop ──────────────────────────────────────────────────
        iteration_limit = max_iterations or cls.MAX_ITERATIONS
        original_length = len(text)

        def substitute_variable(match: re.Match) -> str:
            """Replace matched {{variable}} with its value or leave unchanged."""
            var_name = match.group(1)
            return filtered_variables.get(var_name, match.group(0))

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
            expansion_ratio = len(text) / original_length if original_length > 0 else 1.0
            if len(text) > original_length * cls.MAX_EXPANSION_RATIO:
                logger.warning(
                    "Variable interpolation caused excessive expansion: "
                    "ratio=%.2f, size=%d bytes (max %d bytes)",
                    expansion_ratio, len(text), original_length * cls.MAX_EXPANSION_RATIO,
                )
                raise SecurityError(
                    f"Variable interpolation caused excessive expansion "
                    f"({len(text)} bytes vs {original_length * cls.MAX_EXPANSION_RATIO} max)"
                )
            
            # Double-check absolute size limit
            if len(text.encode("utf-8")) > cls.MAX_OUTPUT_BYTES:
                logger.warning(
                    "Variable interpolation output exceeds maximum absolute size: %d bytes > %d bytes",
                    len(text.encode("utf-8")), cls.MAX_OUTPUT_BYTES,
                )
                raise SecurityError(
                    f"Variable interpolation output exceeds maximum size "
                    f"({len(text.encode('utf-8'))} bytes, max {cls.MAX_OUTPUT_BYTES} bytes)"
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
            request = _dc_replace(request)
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
            raise ValidationError("Text must be a string")
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
            raise ValidationError("Text must be a string")
        return bool(cls.VARIABLE_PATTERN.search(text))
