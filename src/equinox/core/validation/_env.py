"""Environment-variable name and value validation."""

from __future__ import annotations

from equinox.core.exceptions import ValidationError

from ._base import _Guards, _Limits, _Patterns

__all__ = ["_EnvVarValidator"]


class _EnvVarValidator:
    """Environment-variable name and value validation."""

    @classmethod
    def validate(cls, name: str, value: str) -> tuple[str, str]:
        _Guards.require_nonempty_str(name, "Variable name")

        if not isinstance(value, str):
            raise ValidationError("Variable value must be a string")

        if not _Patterns.VARIABLE_NAME.match(name):
            raise ValidationError(
                f"Invalid variable name '{name}'. "
                "Must start with a letter or underscore and contain only "
                "letters, digits, and underscores.",
            )

        if len(name) > _Limits.MAX_VARIABLE_NAME_LENGTH:
            raise ValidationError("Variable name too long")

        if len(value) > _Limits.MAX_VARIABLE_VALUE_LENGTH:
            raise ValidationError("Variable value too long")

        for rx in _Patterns.COMMAND_INJECTION:
            if rx.search(value):
                raise ValidationError("Variable value contains a potentially dangerous pattern")

        return name, value
