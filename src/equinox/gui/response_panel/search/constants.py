"""
Immutable configuration constants for the search system.
No side effects. No runtime mutation.
"""
from __future__ import annotations

# Limits
MAX_MATCHES = 200
DEBOUNCE_INTERVAL_MS = 250
ASYNC_MIN_DOC_CHARS = 20_000

# JSONPath preview limits
PREVIEW_VALUE_LIMIT = 50
PREVIEW_MAX_VALUES = 6

# UI sizes
INPUT_HEIGHT = 24
BUTTON_SIZE = 24
BUTTON_WIDE_SIZE = 28
CANCEL_BUTTON_SIZE = 20
SEARCH_HIGHLIGHT_RADIUS = 5

# Colors
HIGHLIGHT_DIM_COLOR = "#ffd75f"
HIGHLIGHT_CURRENT_COLOR = "#e8a030"

# Error messages
ERROR_NO_JSON = "No JSON document available ÔÇö send a request that returns JSON first."
ERROR_JSONPATH_IMPORT = "ÔÜá jsonpath-ng is not installed. Run: pip install jsonpath-ng"

# Status messages
STATUS_SEARCHING = "searchingÔÇª"
STATUS_NO_MATCHES = "no matches"
STATUS_CANCELLED = "cancelled"
STATUS_INVALID_REGEX = "invalid regex"
STATUS_JSONPATH_ERROR = "expression error"
STATUS_JSONPATH_MISSING = "jsonpath-ng missing"

# UI text
PLACEHOLDER_TEXT_FIND = "Find in bodyÔÇª"
PLACEHOLDER_TEXT_REGEX = "Regular expressionÔÇª"
PLACEHOLDER_TEXT_JSONPATH = "JSONPath expression ÔÇö e.g. $.users[*].name"
