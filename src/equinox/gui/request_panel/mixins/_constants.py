"""Shared constants for the request-panel mixin modules.

Centralises magic numbers, prefixes, dispatch tables, and compiled
patterns so they are defined in exactly one place and importable by
both ``_send_mixin`` and ``_auth_mixin`` (as well as unit tests).
"""

from __future__ import annotations

import re
from typing import Tuple

from equinox.auth import BearerAuth, BasicAuth, APIKeyAuth, OAuth2Auth

# ──────────────────────────────────────────────────────────────────────────────
# Auth
# ──────────────────────────────────────────────────────────────────────────────

# Auth inheritance source encoding prefix
# e.g. "folder:Api/v2" means auth came from the folder named "Api/v2"
FOLDER_AUTH_PREFIX = "folder:"

# Keys excluded from auth config comparison (volatile token state)
AUTH_VOLATILE_KEYS = frozenset({
    "has_access_token", "has_refresh_token", "expires_at",
    "access_token", "refresh_token", "token_timeout",
})

# Auth-type preflight checks: (type, attribute_to_check, warning_message)
AUTH_PREFLIGHT_CHECKS: Tuple[Tuple[type, str, str], ...] = (
    (BearerAuth, "token", "Bearer token is empty"),
    (BasicAuth, "username", "Basic auth username is empty"),
    (APIKeyAuth, "value", "API key value is empty"),
    (OAuth2Auth, "token_url", "OAuth2 token URL is not configured"),
)

# Auth display dispatch: (auth_type, method_name)
AUTH_DISPLAY_DISPATCH: Tuple[Tuple[type, str], ...] = (
    (BasicAuth, "_display_basic_auth"),
    (BearerAuth, "_display_bearer_auth"),
    (OAuth2Auth, "_display_oauth2_auth"),
    (APIKeyAuth, "_display_apikey_auth"),
)

# API key preview length for masked display
APIKEY_PREVIEW_LENGTH = 4

# Auth display layout margins
AUTH_TAB_MARGINS = (6, 8, 6, 8)

# ──────────────────────────────────────────────────────────────────────────────
# URL / Preflight
# ──────────────────────────────────────────────────────────────────────────────

# Pre-compiled URL scheme regex for preflight checks
HTTP_SCHEME_RE = re.compile(r'^https?://', re.IGNORECASE)

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

