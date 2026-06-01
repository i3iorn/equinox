from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import jsonschema

from .models import JsonErrorDetail


class JsonConversionError(Exception):
    """Raised when JSON parsing or serialization fails safely."""

    def __init__(self, detail: JsonErrorDetail) -> None:
        super().__init__(detail.message)
        self.detail = detail


def validate_structure_limits(
    obj: Any,
    *,
    max_depth: int | None = None,
    max_key_count: int | None = None,
    max_array_length: int | None = None,
) -> None:
    """Validate depth, key count, and array length constraints."""

    def walk(node: Any, depth: int) -> None:
        _validate_depth(depth, max_depth)
        if isinstance(node, dict):
            _validate_key_count(len(node), max_key_count)
            for value in node.values():
                walk(value, depth + 1)
            return
        if isinstance(node, list):
            _validate_array_length(len(node), max_array_length)
            for item in node:
                walk(item, depth + 1)

    walk(obj, depth=1)


def _validate_depth(depth: int, max_depth: int | None) -> None:
    if max_depth is None or depth <= max_depth:
        return
    raise JsonConversionError(
        JsonErrorDetail(
            message="JSON structure exceeds maximum allowed depth.",
            context=f"max_depth={max_depth}",
        ),
    )


def _validate_key_count(count: int, max_key_count: int | None) -> None:
    if max_key_count is None or count <= max_key_count:
        return
    raise JsonConversionError(
        JsonErrorDetail(
            message="Object exceeds maximum allowed key count.",
            context=f"max_key_count={max_key_count}",
        ),
    )


def _validate_array_length(length: int, max_array_length: int | None) -> None:
    if max_array_length is None or length <= max_array_length:
        return
    raise JsonConversionError(
        JsonErrorDetail(
            message="Array exceeds maximum allowed length.",
            context=f"max_array_length={max_array_length}",
        ),
    )


def validate_schema(obj: Any, schema: Mapping[str, Any] | None) -> None:
    """Validate an object against a JSON Schema when provided."""
    if schema is None:
        return
    try:
        jsonschema.validate(instance=obj, schema=dict(schema))
    except jsonschema.ValidationError as exc:
        raise JsonConversionError(
            JsonErrorDetail(
                message="JSON does not conform to schema.",
                context=str(exc),
            ),
        ) from exc
