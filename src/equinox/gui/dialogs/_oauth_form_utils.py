"""Shared helpers for OAuth dialog form parsing."""

from __future__ import annotations

import json
from typing import Any


def parse_json_object_field(
    raw_text: str, field_name: str = "Extra Params"
) -> tuple[dict[str, Any] | None, str | None]:
    """Parse a JSON object field, returning ``(value, error)``.

    Blank input is treated as an empty object.
    """
    raw_text = raw_text.strip()
    if not raw_text:
        return {}, None

    try:
        value = json.loads(raw_text)
        if not isinstance(value, dict):
            raise ValueError("must be a JSON object")
        return value, None
    except (json.JSONDecodeError, ValueError) as exc:
        return None, f"{field_name} must be a valid JSON object:\n{exc}"


def parse_json_object_field_lenient(raw_text: str) -> dict[str, Any]:
    """Parse a JSON object field, returning ``{}`` on any invalid input."""
    value, _error = parse_json_object_field(raw_text)
    return value or {}
