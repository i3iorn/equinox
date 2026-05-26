from __future__ import annotations

from .patterns import TIMESTAMP_RE


def detect_string_token_type(value: str, enable_timestamps: bool) -> str:
    """Return token type for a string value."""
    if enable_timestamps and TIMESTAMP_RE.fullmatch(value):
        return "TIMESTAMP"
    return "STRING"
