"""Request body validation."""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from equinox.core.exceptions import ValidationError
from ._base import _Limits, _Patterns

__all__ = ["_BodyValidator"]

_logger = logging.getLogger(__name__)


class _BodyValidator:
    """Request body validation."""

    @classmethod
    def validate(cls, body: Any, content_type: Optional[str] = None) -> Any:
        if body is None:
            return None

        body_str = cls._coerce_to_str(body)
        cls._check_size(body_str)

        if content_type and "json" in content_type.lower() and isinstance(body, str):
            cls._validate_json(body)

        if isinstance(body, str):
            cls._warn_if_sql_injection(body)

        return body

    # -- private helpers -----------------------------------------------------

    @staticmethod
    def _coerce_to_str(body: Any) -> str:
        if isinstance(body, dict):
            try:
                return json.dumps(body)
            except (TypeError, ValueError) as exc:
                raise ValidationError(f"Invalid JSON body: {exc}")
        return str(body)

    @staticmethod
    def _check_size(body_str: str) -> None:
        size = len(body_str.encode("utf-8"))
        if size > _Limits.MAX_BODY_SIZE:
            raise ValidationError(
                f"Request body too large: {size:,} bytes "
                f"(max: {_Limits.MAX_BODY_SIZE:,} bytes)"
            )

    @staticmethod
    def _validate_json(body: str) -> None:
        """Accept valid JSON; tolerate trailing commas as a single deviation."""
        try:
            json.loads(body)
        except json.JSONDecodeError as original_err:
            normalised = _Patterns.TRAILING_COMMA_JSON.sub(r"\1", body)
            try:
                json.loads(normalised)
            except json.JSONDecodeError:
                raise ValidationError(f"Invalid JSON body: {original_err}")

    @staticmethod
    def _warn_if_sql_injection(body: str) -> None:
        for rx in _Patterns.SQL_INJECTION:
            if rx.search(body):
                _logger.warning(
                    "Potential SQL injection pattern detected in request body"
                )
                break   # one warning per body is enough

