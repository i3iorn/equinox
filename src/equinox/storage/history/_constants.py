"""Shared constants for the history package."""

from __future__ import annotations

__all__ = [
    "_LIKE_ESCAPE_CLAUSE",
    "_STATUS_CODE_RANGES",
    "_HISTORY_LIST_COLUMNS",
    "_HISTORY_STREAM_COLUMNS",
]

# SQLite LIKE escape clause — used with ESCAPE '\\' so % and _ match literally.
_LIKE_ESCAPE_CLAUSE = "ESCAPE '\\'"

# HTTP status-code class ranges used by the search filter.
_STATUS_CODE_RANGES: dict[str, tuple[int, int]] = {
    "2xx": (200, 299),
    "3xx": (300, 399),
    "4xx": (400, 499),
    "5xx": (500, 599),
}

# Column projections for list/search fast paths. ``request_body`` and
# ``response_body`` hold the full BLOBs (up to MAX_BODY_SIZE each); they are
# only fetched by the detail view (``get_history``). The streaming post-filter
# path still needs ``response_body``/``response_headers`` for regex/JSONPath/
# header matching, so it keeps those but drops ``request_body``.
_HISTORY_LIST_COLUMNS = (
    "id, request_id, method, url, status_code, reason, elapsed, error, "
    "executed_at, request_headers, response_headers, request_correlation_id"
)
_HISTORY_STREAM_COLUMNS = (
    "id, request_id, method, url, status_code, reason, elapsed, error, "
    "executed_at, request_headers, response_headers, response_body, "
    "request_correlation_id"
)
