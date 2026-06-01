# json_tools/utils.py
from __future__ import annotations

from collections.abc import Generator
from typing import Any


def tag_value(value: Any, tag: str) -> dict[str, Any]:
    """Wrap a value in a lightweight tagged-object envelope."""
    return {"__tag__": tag, "value": value}


def strip_tags(obj: Any) -> Any:
    """Recursively unwrap values previously wrapped by :func:`tag_value`."""
    if isinstance(obj, dict) and "__tag__" in obj:
        return obj["value"]
    if isinstance(obj, dict):
        return {k: strip_tags(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [strip_tags(v) for v in obj]
    return obj


def walk(obj: Any) -> Generator[tuple[str, Any]]:
    """Yield every dictionary key/value pair in a nested JSON-like structure."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k, v
            yield from walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk(v)
