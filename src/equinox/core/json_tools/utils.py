# json_tools/utils.py
from __future__ import annotations

from typing import Any


def tag_value(value: Any, tag: str):
    return {"__tag__": tag, "value": value}


def strip_tags(obj: Any):
    if isinstance(obj, dict) and "__tag__" in obj:
        return obj["value"]
    if isinstance(obj, dict):
        return {k: strip_tags(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [strip_tags(v) for v in obj]
    return obj


def walk(obj: Any):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k, v
            yield from walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk(v)
