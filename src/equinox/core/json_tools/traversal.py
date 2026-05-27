from __future__ import annotations

from collections.abc import Generator, Iterable
from typing import Any

from .decoder import JsonDecoder
from .models import EventType, JsonErrorDetail
from .validation import JsonConversionError, validate_structure_limits


def iter_json_lines(text: str) -> Iterable[str]:
    """Yield non-empty JSON chunks from newline-delimited text."""
    for line in text.splitlines():
        chunk = line.strip()
        if chunk:
            yield chunk


def stream_json_objects(
    text: str,
    *,
    max_depth: int | None = None,
    max_key_count: int | None = None,
    max_array_length: int | None = None,
) -> Generator[Any, None, None]:
    """Yield one parsed object per non-empty line of JSON or JSONC text."""
    decoder = JsonDecoder(allow_comments=True)
    for chunk in iter_json_lines(text):
        try:
            obj = decoder.loads_jsonc(chunk)
        except Exception as exc:
            raise JsonConversionError(
                JsonErrorDetail(
                    message="Failed to parse JSON chunk.",
                    context=str(exc),
                )
            ) from exc
        validate_structure_limits(
            obj,
            max_depth=max_depth,
            max_key_count=max_key_count,
            max_array_length=max_array_length,
        )
        yield obj


def sax_events(obj: Any) -> Generator[tuple[EventType, Any], None, None]:
    """Generate SAX-style events from a parsed JSON structure."""
    if isinstance(obj, dict):
        yield ("start_object", None)
        for key, value in obj.items():
            yield ("key", key)
            yield from sax_events(value)
        yield ("end_object", None)
        return
    if isinstance(obj, list):
        yield ("start_array", None)
        for item in obj:
            yield from sax_events(item)
        yield ("end_array", None)
        return
    yield ("value", obj)
