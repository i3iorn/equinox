from __future__ import annotations

import logging
import time
from typing import Any, Mapping

from .decoder import JsonDecoder
from .encoder import safe_json_dumps
from .models import JsonErrorDetail, JsonResult
from .traversal import sax_events, stream_json_objects
from .validation import JsonConversionError, validate_schema, validate_structure_limits

logger = logging.getLogger(__name__)


def json_to_object(
    text: str,
    *,
    max_length: int | None = None,
    max_depth: int | None = None,
    max_key_count: int | None = None,
    max_array_length: int | None = None,
    streaming: bool = False,
    schema: Mapping[str, Any] | None = None,
) -> JsonResult:
    """Convert JSON or JSONC text into a Python object with safety guards."""
    if not isinstance(text, str):
        raise JsonConversionError(JsonErrorDetail("Input must be a string."))
    stripped = text.strip()
    if not stripped:
        raise JsonConversionError(JsonErrorDetail("Input JSON text is empty."))
    if max_length is not None and len(stripped) > max_length:
        raise JsonConversionError(
            JsonErrorDetail(
                message="Input exceeds maximum allowed length.",
                context=f"max_length={max_length}",
            )
        )

    start_time = time.perf_counter()
    parsed = _parse_input(
        stripped,
        streaming=streaming,
        max_depth=max_depth,
        max_key_count=max_key_count,
        max_array_length=max_array_length,
    )
    validate_schema(parsed, schema)
    elapsed_ms = int((time.perf_counter() - start_time) * 1000)
    logger.info(
        "json_tools.json_to_object input_length=%d elapsed_ms=%d streaming=%s",
        len(text),
        elapsed_ms,
        streaming,
    )
    return JsonResult(value=parsed, elapsed_ms=elapsed_ms)


def _parse_input(
    text: str,
    *,
    streaming: bool,
    max_depth: int | None,
    max_key_count: int | None,
    max_array_length: int | None,
) -> Any:
    if streaming:
        return list(
            stream_json_objects(
                text,
                max_depth=max_depth,
                max_key_count=max_key_count,
                max_array_length=max_array_length,
            )
        )
    decoder = JsonDecoder(allow_comments=True)
    try:
        parsed = decoder.loads_jsonc(text)
    except Exception as exc:
        raise JsonConversionError(
            JsonErrorDetail("Failed to parse JSON input.", context=str(exc))
        ) from exc
    validate_structure_limits(
        parsed,
        max_depth=max_depth,
        max_key_count=max_key_count,
        max_array_length=max_array_length,
    )
    return parsed


def json_to_str(
    obj: Any,
    *,
    indent: int = 2,
    ensure_ascii: bool = False,
    sort_keys: bool = False,
    max_length: int | None = None,
    max_depth: int | None = None,
    max_key_count: int | None = None,
    max_array_length: int | None = None,
    schema: Mapping[str, Any] | None = None,
) -> JsonResult:
    """Serialize a Python object into JSON with structural guards."""
    validate_structure_limits(
        obj,
        max_depth=max_depth,
        max_key_count=max_key_count,
        max_array_length=max_array_length,
    )
    validate_schema(obj, schema)

    start_time = time.perf_counter()
    try:
        json_text = safe_json_dumps(
            obj,
            indent=indent,
            ensure_ascii=ensure_ascii,
            sort_keys=sort_keys,
        )
    except Exception as exc:
        raise JsonConversionError(
            JsonErrorDetail("Failed to serialize object to JSON.", context=str(exc))
        ) from exc
    if max_length is not None and len(json_text) > max_length:
        raise JsonConversionError(
            JsonErrorDetail(
                message="Output exceeds maximum allowed length.",
                context=f"max_length={max_length}",
            )
        )

    elapsed_ms = int((time.perf_counter() - start_time) * 1000)
    logger.info(
        "json_tools.json_to_str output_length=%d elapsed_ms=%d",
        len(json_text),
        elapsed_ms,
    )
    return JsonResult(value=json_text, elapsed_ms=elapsed_ms)


__all__ = [
    "JsonConversionError",
    "JsonErrorDetail",
    "JsonResult",
    "json_to_object",
    "json_to_str",
    "sax_events",
    "stream_json_objects",
]
