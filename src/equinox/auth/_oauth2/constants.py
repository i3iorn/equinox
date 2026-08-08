"""Constants used by OAuth2 authentication internals."""

_MAX_TOKEN_RETRIES = 3
# Assume a 1-hour lifetime when the server omits the ``expires_in`` field.
_DEFAULT_TOKEN_EXPIRY_SECONDS = 3600

# Token timeout bounds (seconds)
_MIN_TOKEN_TIMEOUT: float = 0.1
_MAX_TOKEN_TIMEOUT: float = 300.0

# Lock acquisition timeout to prevent deadlock (seconds)
_LOCK_TIMEOUT: float = 5.0

# Token snapshot — fields whose values are partially redacted for safe display.
_REDACTABLE_TOKEN_FIELDS: frozenset[str] = frozenset({"access_token", "refresh_token", "id_token"})

# Response headers excluded from the token-response snapshot (may contain secrets).
_FILTERED_RESPONSE_HEADERS: frozenset[str] = frozenset({"set-cookie"})

# Token preview parameters: show first N + "..." + last M chars when len > MIN.
_TOKEN_REDACT_PREFIX_LEN: int = 8
_TOKEN_REDACT_SUFFIX_LEN: int = 4
_TOKEN_REDACT_MIN_LEN: int = 12

# Maximum raw response text length kept in the token-response snapshot fallback.
_TOKEN_RESPONSE_RAW_MAX: int = 2000

# Independent connect-timeout cap for token-endpoint requests so that a dead
# proxy fails fast at TCP level without waiting the full token_timeout.
_MAX_CONNECT_TIMEOUT: float = 5.0

# Base for the exponential backoff between token-request retries (seconds).
_RETRY_BACKOFF_BASE: int = 2

# Markers that identify non-retryable "nothing is listening" errors.
_CONNECTION_REFUSED_MARKERS: tuple[str, ...] = ("10061", "connection refused", "econnrefused")

# OAuth2 token errors that indicate refresh_token grant cannot be used and the
# client should retry with client_credentials when available.
_REFRESH_GRANT_FALLBACK_ERRORS: frozenset[str] = frozenset(
    {
        "invalid_grant",
        "unsupported_grant_type",
    },
)

# Hex-suffix length appended to anonymous (no client_id) storage keys.
_ANON_KEY_ID_LENGTH: int = 12

# Use cached token up to 30s past expiry.
_GRACE_PERIOD_SECONDS = 30
