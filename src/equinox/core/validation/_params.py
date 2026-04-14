"""Query-parameter key/value validation."""
from __future__ import annotations

from typing import Any, Dict

from equinox.core.exceptions import ValidationError
from ._limits import _Limits
from ._guards import _Guards

__all__ = ["_ParamValidator"]


class _ParamValidator:
    """Query-parameter key/value validation."""

    @classmethod
    def validate(cls, params: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(params, dict):
            raise ValidationError("Query parameters must be a dictionary")

        if len(params) > _Limits.MAX_PARAM_COUNT:
            raise ValidationError(
                f"Too many parameters (max: {_Limits.MAX_PARAM_COUNT})"
            )

        return {cls._validate_key(k): cls._validate_value(v) for k, v in params.items()}

    # -- private helpers -----------------------------------------------------

    @staticmethod
    def _validate_key(key: str) -> str:
        if not isinstance(key, str):
            raise ValidationError("Parameter key must be a string")
        if len(key) > _Limits.MAX_PARAM_KEY_LENGTH:
            raise ValidationError("Parameter key too long")
        _Guards.check_crlf(key, f"Parameter key '{key}'")
        return key

    @staticmethod
    def _validate_value(value: Any) -> Any:
        value_str = str(value)
        if len(value_str) > _Limits.MAX_PARAM_VALUE_LENGTH:
            raise ValidationError("Parameter value too long")
        _Guards.check_crlf(value_str, "Parameter value")
        return value

