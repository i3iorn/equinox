"""Shared constants for the history package."""
from __future__ import annotations

from typing import Dict, Tuple

__all__ = ["_LIKE_ESCAPE_CLAUSE", "_STATUS_CODE_RANGES"]

# SQLite LIKE escape clause — used with ESCAPE '\\' so % and _ match literally.
_LIKE_ESCAPE_CLAUSE = "ESCAPE '\\'"

# HTTP status-code class ranges used by the search filter.
_STATUS_CODE_RANGES: Dict[str, Tuple[int, int]] = {
    "2xx": (200, 299),
    "3xx": (300, 399),
    "4xx": (400, 499),
    "5xx": (500, 599),
}

