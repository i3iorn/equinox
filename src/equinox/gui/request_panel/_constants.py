"""Shared constants for the request-panel mixin modules.

Centralises magic numbers, prefixes, dispatch tables, and compiled
patterns so they are defined in exactly one place and importable by
both ``_send_mixin`` and ``_auth_mixin`` (as well as unit tests).

Auth display and preflight checks are now derived from the strategy
classes themselves (via ``get_display_summary`` / ``get_preflight_warning``),
eliminating the need for parallel isinstance dispatch tables.
"""

from __future__ import annotations

import re

# ──────────────────────────────────────────────────────────────────────────────
# Auth
# ──────────────────────────────────────────────────────────────────────────────

# Auth inheritance source encoding prefix
# e.g. "folder:Api/v2" means auth came from the folder named "Api/v2"
FOLDER_AUTH_PREFIX = "folder:"

# Keys excluded from auth config comparison (volatile token state)
AUTH_VOLATILE_KEYS = frozenset(
    {
        "has_access_token",
        "has_refresh_token",
        "expires_at",
        "access_token",
        "refresh_token",
        "token_timeout",
    }
)


# Auth display layout margins
AUTH_TAB_MARGINS = (6, 8, 6, 8)

# ──────────────────────────────────────────────────────────────────────────────
# URL / Preflight
# ──────────────────────────────────────────────────────────────────────────────

# Pre-compiled URL scheme regex for preflight checks
HTTP_SCHEME_RE = re.compile(r"^https?://", re.IGNORECASE)

# Preflight warning separator
PREFLIGHT_SEPARATOR = "  ·  "

# ──────────────────────────────────────────────────────────────────────────────
# Status / Timing
# ──────────────────────────────────────────────────────────────────────────────

# UI status message durations (ms)
STATUS_DURATION_SHORT = 4000
STATUS_DURATION_LONG = 8000

# Worker cancellation wait time (ms)
WORKER_WAIT_MS = 2000

# ──────────────────────────────────────────────────────────────────────────────
# Recommender
# ──────────────────────────────────────────────────────────────────────────────

# Number of suggestion results to request
RECOMMENDER_TOP_N = 5

# Confidence threshold for WARNING severity
RECOMMENDER_HIGH_CONFIDENCE = 0.75

# ──────────────────────────────────────────────────────────────────────────────
# Panel UI — widget dimensions and limits
# ──────────────────────────────────────────────────────────────────────────────

METHOD_COMBO_WIDTH = 90
SEND_BTN_WIDTH = 80
CANCEL_BTN_WIDTH = 70
BROWSE_BTN_WIDTH = 70
IMPORT_BTN_WIDTH = 140
BENCHMARK_BTN_WIDTH = 100
CLEAR_SV_BTN_WIDTH = 140
FMT_JSON_BTN_WIDTH = 95

# URL auto-completer
HISTORY_COMPLETER_LIMIT = 200
COMPLETER_MAX_VISIBLE = 12

# ──────────────────────────────────────────────────────────────────────────────
# Save Dialog
# ──────────────────────────────────────────────────────────────────────────────

# Save-request dialog minimum width
SAVE_DIALOG_MIN_WIDTH = 420

# URL preview length in auto-generated request name
SAVE_DIALOG_URL_PREVIEW_LEN = 50
