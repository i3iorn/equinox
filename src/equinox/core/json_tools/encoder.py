from __future__ import annotations

import json
from typing import Any

from equinox.core.exceptions import SecurityError


def safe_json_dumps(
    obj: Any,
    *,
    max_len: int | None = None,
    indent: int | None = None,
    ensure_ascii: bool = True,
    sort_keys: bool = False,
) -> str:
    """Serialize an object to JSON with an optional maximum output length."""
    if max_len is not None and max_len < 0:
        raise ValueError("max_len must be non-negative")
    s = json.dumps(obj, indent=indent, ensure_ascii=ensure_ascii, sort_keys=sort_keys)
    if max_len is not None and len(s) > max_len:
        raise SecurityError(f"JSON serialization exceeds {max_len} bytes")
    return s
