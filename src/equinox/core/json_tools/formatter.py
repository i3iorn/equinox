# json_tools/formatter.py
from __future__ import annotations

import logging
import time
from typing import Tuple

from .decoder import JsonDecoder
from .encoder import safe_json_dumps

logger = logging.getLogger(__name__)


def format_json(
    text: str,
    *,
    indent: int = 2,
    ensure_ascii: bool = False,
    sort_keys: bool = False,
) -> Tuple[str, int]:
    """
    Pretty‑print JSON or JSONC text using json_tools.

    Returns:
        (formatted_text, elapsed_ms)

    Raises:
        JsonParseError: if the input cannot be parsed
        SecurityError: if output exceeds max_len (if used)
    """
    if not text.strip():
        return text, 0

    t0 = time.perf_counter()

    decoder = JsonDecoder(allow_comments=True)

    # Parse JSON or JSONC
    data = decoder.loads_jsonc(text)

    # Pretty‑print using safe encoder
    formatted = safe_json_dumps(
        data,
        indent=indent,
        ensure_ascii=ensure_ascii,
        sort_keys=sort_keys,
    )

    elapsed_ms = int((time.perf_counter() - t0) * 1000)

    logger.info(
        "json_tools.format_json original_length=%d formatted_length=%d elapsed_ms=%d",
        len(text),
        len(formatted),
        elapsed_ms,
    )

    return formatted, elapsed_ms
