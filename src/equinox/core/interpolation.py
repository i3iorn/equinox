"""Variable interpolation for requests"""

import re
import logging
from typing import Dict, Any, Optional

from equinox.core.exceptions import ValidationError, SecurityError

logger = logging.getLogger(__name__)


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
        ``{{base}}`` → ``{{scheme}}://host`` resolve fully.
        Unresolvable placeholders are left unchanged.

        Args:
            text: Text containing {{variable}} placeholders
            variables: Dictionary of variable values
            max_iterations: Maximum interpolation passes (default: 10)

        Returns:
            Text with variables replaced

        Raises:
            ValidationError: If input is invalid
            SecurityError: If interpolation causes excessive expansion
        """
        if not isinstance(text, str):
            raise ValidationError("Text must be a string")

        if not isinstance(variables, dict):
            raise ValidationError("Variables must be a dictionary")

        if not text or not variables:
            return text

        iteration_limit = max_iterations or cls.MAX_ITERATIONS
        original_length = len(text)

        def substitute_variable(match: re.Match) -> str:
            return variables.get(match.group(1), match.group(0))

        for _ in range(iteration_limit):
            previous_text = text
            text = cls.VARIABLE_PATTERN.sub(substitute_variable, text)

            if text == previous_text:
                break

            if len(text) > original_length * cls.MAX_EXPANSION_RATIO:
                raise SecurityError("Variable interpolation caused excessive text expansion")
            if len(text.encode("utf-8")) > cls.MAX_OUTPUT_BYTES:
                raise SecurityError("Variable interpolation output exceeds maximum size (1 MB)")
        else:
            logger.warning("Variable interpolation reached max iterations (%d)", iteration_limit)

        return text

    @classmethod
    def interpolate_request(cls, o_request: Any, variables: Dict[str, str]) -> Any:
        """Interpolate variables in a Request object in-place.

        Args:
            o_request: Request object to modify
            variables: Dictionary of variable values
        """
        request = o_request.copy()

        if not variables:
            return

        if hasattr(request, 'url') and request.url:
            request.url = cls.interpolate(request.url, variables)

        if hasattr(request, 'headers') and request.headers:
            request.headers = {
                cls.interpolate(str(key), variables): cls.interpolate(str(value), variables)
                for key, value in request.headers.items()
            }

        if hasattr(request, 'params') and request.params:
            request.params = {
                cls.interpolate(str(key), variables): cls.interpolate(str(value), variables)
                for key, value in request.params.items()
            }

        if hasattr(request, 'body') and request.body:
            request.body = cls.interpolate(request.body, variables)

        if hasattr(request, 'name') and request.name:
            request.name = cls.interpolate(request.name, variables)

        if hasattr(request, 'description') and request.description:
            request.description = cls.interpolate(request.description, variables)

        return request

    @classmethod
    def find_variables(cls, text: str) -> list:
        """Find all unique variable names referenced in text.

        Args:
            text: Text to search

        Returns:
            List of unique variable names found
        """
        if not isinstance(text, str):
            return []
        return list(set(cls.VARIABLE_PATTERN.findall(text)))

    @classmethod
    def has_variables(cls, text: str) -> bool:
        """Return True if text contains any {{variable}} placeholders.

        Args:
            text: Text to check
        """
        if not isinstance(text, str):
            return False
        return bool(cls.VARIABLE_PATTERN.search(text))
