"""OAuth2 authentication with secure token storage and refresh"""

import base64
import json
import os
import time
import logging
import uuid
import random
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Any, Callable, Dict, Literal, Optional

import httpx

from equinox.auth._base import AuthStrategy, _validate_credential, _interpolate_field, AuthError
from equinox.security.secure_storage import SecureStorage
from equinox.core.audit import get_audit_logger, AuditEventType, AuditLevel
from equinox.security import mask_secret, sanitize_details, redact_url
from equinox.core.util.time import utc_now
from equinox.core.validation import Validator

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Module constants
# ──────────────────────────────────────────────────────────────────────────────

_MAX_TOKEN_RETRIES = 3
# Assume a 1-hour lifetime when the server omits the ``expires_in`` field.
_DEFAULT_TOKEN_EXPIRY_SECONDS = 3600

# Token timeout bounds (seconds)
_MIN_TOKEN_TIMEOUT: float = 0.1
_MAX_TOKEN_TIMEOUT: float = 300.0

# Lock acquisition timeout to prevent deadlock (seconds)
_LOCK_TIMEOUT: float = 5.0

# Token snapshot — fields whose values are partially redacted for safe display.
_REDACTABLE_TOKEN_FIELDS: frozenset = frozenset({"access_token", "refresh_token", "id_token"})

# Response headers excluded from the token-response snapshot (may contain secrets).
_FILTERED_RESPONSE_HEADERS: frozenset = frozenset({"set-cookie"})

# Token preview parameters: show first N + "…" + last M chars when len > MIN.
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
_CONNECTION_REFUSED_MARKERS: tuple = ("10061", "connection refused", "econnrefused")

# Hex-suffix length appended to anonymous (no client_id) storage keys.
_ANON_KEY_ID_LENGTH: int = 12

_GRACE_PERIOD_SECONDS = 30      # Use cached token up to 30s past expiry


# ──────────────────────────────────────────────────────────────────────────────
# Module-level helpers
# ──────────────────────────────────────────────────────────────────────────────

def make_oauth2_basic_auth_header(client_id: str, client_secret: str) -> str:
    """Return an RFC 6749 §2.3.1 HTTP Basic Authorization header value.

    Encodes ``client_id:client_secret`` in Base64 and returns the full header
    value string (``"Basic <encoded>"``).

    Raises:
        AuthError: If *client_id* or *client_secret* is empty.
    """
    if not client_id or not client_secret:
        raise AuthError(
            "Client ID and secret are required for Basic auth token endpoint"
        )
    credentials = f"{client_id}:{client_secret}"
    encoded = base64.b64encode(credentials.encode("utf-8")).decode("ascii")
    return f"Basic {encoded}"


def _is_connection_refused(exc: Exception) -> bool:
    """Return True for ConnectErrors that indicate nothing is listening.

    These errors are structural (the port is closed) and will never succeed
    on retry, so the retry loop should break immediately.

    Args:
        exc: Exception raised by httpx during the token request.
    """
    if not isinstance(exc, httpx.ConnectError):
        return False
    lower = str(exc).lower()
    return any(marker in lower for marker in _CONNECTION_REFUSED_MARKERS)


def _redact_token_value(key: str, value: Any) -> Any:
    """Return a display-safe preview of *value* when *key* is a known token field.

    Long token strings (> ``_TOKEN_REDACT_MIN_LEN`` chars) are shortened to
    ``first8chars…last4chars``.  Short values and non-token keys pass through.

    Args:
        key:   Response body key (e.g. ``"access_token"``).
        value: Corresponding value from the token endpoint response.
    """
    if (
        key in _REDACTABLE_TOKEN_FIELDS
        and isinstance(value, str)
        and len(value) > _TOKEN_REDACT_MIN_LEN
    ):
        return value[:_TOKEN_REDACT_PREFIX_LEN] + "…" + value[-_TOKEN_REDACT_SUFFIX_LEN:]
    return value


class OAuth2Auth(AuthStrategy):
    """OAuth2 authentication with token management and secure storage.

    Features:
    - Automatic token refresh before expiration
    - Secure token storage (AES-256 encrypted)
    - Support for refresh token and client credentials flows
    - Configurable refresh buffer (e.g., refresh 30s before expiry)
    """

    AUTH_TYPE = "oauth2"
    DISPLAY_NAME = "OAuth 2.0"

    REFRESH_BUFFER_SECONDS = 30
    DEFAULT_TOKEN_TIMEOUT = 10.0

    def __init__(
        self,
        access_token: Optional[str] = None,
        token_url: Optional[str] = None,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        scope: Optional[str] = None,
        refresh_token: Optional[str] = None,
        secure_storage: Optional[SecureStorage] = None,
        storage_key: Optional[str] = None,
        token_timeout: float = 10.0,
        verify_ssl: bool = True,
        token_auth: Literal["body", "basic"] = "body",
        extra_params: Optional[Dict[str, Any]] = None,
    ):
        """Initialize OAuth2 auth with optional secure storage.

        Args:
            access_token: Current access token
            token_url: URL to obtain tokens
            client_id: OAuth2 client ID
            client_secret: OAuth2 client secret
            scope: OAuth2 scope
            refresh_token: Refresh token for obtaining new access tokens
            secure_storage: SecureStorage instance for storing tokens (optional)
            storage_key: Key to store tokens in secure storage
            token_timeout: HTTP timeout for token endpoint requests in seconds
            verify_ssl: Whether to verify TLS certificates for token endpoint requests
            token_auth: How to send credentials to token endpoint — "body" (default, RFC 6749 §2.3.1)
                       or "basic" (HTTP Basic Auth, RFC 6749 §2.3.1, used by D&B Direct+, etc.)
        """
        self.access_token = access_token
        self.token_url = token_url
        self.client_id = client_id
        self.client_secret = client_secret
        self.scope = scope
        self.refresh_token = refresh_token
        self.extra_params = extra_params or {}
        self.expires_at: Optional[datetime] = None

        # Validate token_auth parameter
        if token_auth not in ("body", "basic"):
            raise AuthError(f"Invalid token_auth {token_auth!r}. Must be 'body' or 'basic'.")
        self.token_auth = token_auth

        # Validate and clamp token_timeout
        if not isinstance(token_timeout, (int, float)) or token_timeout <= 0:
            logger.warning(
                "Invalid token_timeout %r, using default %s seconds",
                token_timeout, self.DEFAULT_TOKEN_TIMEOUT
            )
            token_timeout = self.DEFAULT_TOKEN_TIMEOUT

        original_timeout = token_timeout
        self.token_timeout = max(_MIN_TOKEN_TIMEOUT, min(token_timeout, _MAX_TOKEN_TIMEOUT))
        self._verify_ssl = bool(verify_ssl)

        if original_timeout != self.token_timeout:
            logger.debug(
                "Clamped token_timeout from %s to %s seconds",
                original_timeout, self.token_timeout
            )

        self.secure_storage = secure_storage
        # Prevent storage-key collision when client_id is None.
        if storage_key:
            self.storage_key = storage_key
        elif client_id:
            self.storage_key = f"oauth2_{client_id}"
        else:
            self.storage_key = f"oauth2_anonymous_{uuid.uuid4().hex[:_ANON_KEY_ID_LENGTH]}"

        # Tracks whether secure-storage operations are succeeding.
        # Set to False on the first I/O failure so callers / the GUI can
        # surface a prominent warning instead of silently degrading security.
        self._storage_available: bool = True

        # Prevent concurrent token-refresh races
        self._refresh_lock = Lock()
        self._audit = get_audit_logger()

        # Proxy to use for token endpoint requests — set by HTTPClient._apply_auth
        # so token fetches route through the same proxy as the main request.
        self._proxy: Optional[str] = None

        # Last token-endpoint exchange (redacted) — surfaced in the GUI
        self._last_token_response: Optional[Dict[str, Any]] = None

        if self.secure_storage and self.storage_key:
            self._load_from_storage()

    # ── AuthStrategy interface ────────────────────────────────────────────────

    def apply(self, request: Any, headers: Dict[str, str]) -> None:
        """Add Authorization header with OAuth2 bearer token.

        Refreshes the token first when it is missing, expired, or expiring soon.
        Concurrent callers share the same refresh attempt via a lock.

        Args:
            request: The HTTP request object (may be used by subclasses).
            headers: Headers dict to modify in-place.

        Raises:
            AuthError: If token refresh fails, lock timeout occurs, or no token available.
        """
        self.apply_with_context(request, headers, proxy=None, verify_ssl=None)

    def apply_with_context(
        self,
        request: Any,
        headers: Dict[str, str],
        *,
        proxy: Optional[str] = None,
        verify_ssl: Optional[bool] = None,
    ) -> None:
        """Apply auth using optional runtime transport context.

        Args:
            request: Current request object.
            headers: Headers dict to modify in-place.
            proxy: Optional per-request proxy override for token refresh calls.
            verify_ssl: Optional per-request TLS verification override.
        """
        effective_proxy = proxy if proxy is not None else self._proxy
        effective_verify_ssl = (
            bool(verify_ssl) if verify_ssl is not None else self._verify_ssl
        )

        # Acquire lock with timeout to prevent indefinite deadlock
        acquired = self._refresh_lock.acquire(timeout=_LOCK_TIMEOUT)
        if not acquired:
            logger.error("Failed to acquire token refresh lock within %.1f seconds", _LOCK_TIMEOUT)
            raise AuthError(
                f"Token refresh lock timeout — possible deadlock or high contention "
                f"(waited {_LOCK_TIMEOUT}s)"
            )

        try:
            if self._needs_refresh():
                self._refresh_access_token(
                    proxy=effective_proxy,
                    verify_ssl=effective_verify_ssl,
                )
        finally:
            self._refresh_lock.release()

        if not self.access_token:
            raise AuthError("No access token available")

        # Validate the token before injecting into the header — it may
        # originate from an untrusted token endpoint.
        _validate_credential(self.access_token, "OAuth2 access token")

        headers["Authorization"] = f"Bearer {self.access_token}"

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary for storage.

        Includes ``client_secret`` so the object can be fully reconstructed
        via :meth:`from_dict`.  The surrounding storage layer (SQLite, secure
        file) is responsible for protecting this data at rest.
        """
        return {
            "type": "oauth2",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "token_url": self.token_url,
            "scope": self.scope,
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "has_access_token": bool(self.access_token),
            "has_refresh_token": bool(self.refresh_token),
            "expires_at": self._expires_at_iso(),
            "token_timeout": self.token_timeout,
            "verify_ssl": self._verify_ssl,
            "token_auth": self.token_auth,
            "extra_params": self.extra_params,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any], **kwargs: Any) -> "OAuth2Auth":
        """Create from dictionary."""
        secure_storage = kwargs.get("secure_storage")
        instance = cls(
            access_token=data.get("access_token"),
            token_url=data.get("token_url"),
            client_id=data.get("client_id"),
            client_secret=data.get("client_secret"),
            scope=data.get("scope"),
            refresh_token=data.get("refresh_token"),
            secure_storage=secure_storage,
            token_timeout=data.get("token_timeout", cls.DEFAULT_TOKEN_TIMEOUT),
            verify_ssl=data.get("verify_ssl", True),
            token_auth=data.get("token_auth", "body"),
            extra_params=data.get("extra_params") if data.get("extra_params") is not None else None,
        )
        # Restore expiration so _needs_refresh() can make the right decision.
        instance.expires_at = cls._parse_expires_at(data.get("expires_at"))
        return instance

    # ── Strategy metadata ─────────────────────────────────────────────────────

    def interpolate(self, interp: Callable[[str], str]) -> "OAuth2Auth":
        """Return a copy with ``{{VAR}}`` placeholders expanded.

        Preserves non-string state (expires_at, _proxy, _refresh_lock) that
        would be lost by a naive to_dict/from_dict round-trip.
        """
        new_auth = OAuth2Auth(
            token_url=_interpolate_field(self.token_url, interp),
            client_id=_interpolate_field(self.client_id, interp),
            client_secret=_interpolate_field(self.client_secret, interp),
            scope=_interpolate_field(self.scope, interp),
            access_token=_interpolate_field(self.access_token, interp),
            refresh_token=_interpolate_field(self.refresh_token, interp),
            token_timeout=self.token_timeout,
            verify_ssl=self._verify_ssl,
            token_auth=self.token_auth,
            extra_params=self.extra_params,
        )
        # Preserve token expiry so pre-fetched token isn't treated as eternal
        new_auth.expires_at = self.expires_at
        return new_auth

    def get_display_summary(self) -> str:
        return (
            f"Token URL: {self.token_url or '—'}\n"
            f"Client ID: {self.client_id or '—'}"
        )

    def get_preflight_warning(self) -> Optional[str]:
        if not self.token_url:
            return "OAuth2 token URL is not configured"
        return None

    def __repr__(self) -> str:
        token_status = "present" if self.access_token else "None"
        return (
            f"OAuth2Auth(client_id={self.client_id}, "
            f"access_token={token_status}, expires_at={self.expires_at})"
        )

    # ── Token state helpers ───────────────────────────────────────────────────

    @property
    def last_token_response(self) -> Optional[Dict[str, Any]]:
        """The most recent (redacted) token endpoint exchange, or ``None``."""
        return self._last_token_response

    @property
    def _has_storage(self) -> bool:
        """Return True when secure storage is configured and a key is set."""
        return bool(self.secure_storage and self.storage_key)

    @property
    def storage_available(self) -> bool:
        """Return False if a secure-storage I/O error has been observed.

        Once set to ``False``, tokens cannot be persisted between sessions.
        Callers and the GUI should surface a visible warning when this is
        ``False`` so the user knows their tokens are only held in memory.
        """
        return self._storage_available

    @property
    def _proxy_label(self) -> str:
        """Human-readable proxy label used in log messages."""
        return self._proxy or "none"

    @property
    def _proxy_for_httpx(self) -> Optional[str]:
        """Proxy URL passed to httpx, or ``None`` when no proxy is configured."""
        return self._proxy or None

    @property
    def verify_ssl(self) -> bool:
        """Whether TLS certificate verification is enabled for token requests."""
        return self._verify_ssl

    def _needs_refresh(self) -> bool:
        """Return True when the token is missing, expired, or about to expire."""
        if not self.access_token:
            return True

        if not self.expires_at:
            # No expiration info — reuse until the server rejects with 401.
            # Forcing a refresh every request would exhaust client-credentials grants.
            return False

        # Normalise to naive UTC in case expires_at was set directly with tzinfo.
        expiry = self.expires_at
        if expiry.tzinfo is not None:
            expiry = expiry.astimezone(timezone.utc).replace(tzinfo=None)
        seconds_until_expiry = (expiry - utc_now()).total_seconds()
        return seconds_until_expiry <= self.REFRESH_BUFFER_SECONDS

    def _is_token_within_grace_period(self) -> bool:
        """Check if cached token is still usable (within grace window).

        Returns True if:
        - Token has no expiry info (assume valid until rejected by server)
        - Token hasn't expired past the grace window (within _GRACE_PERIOD_SECONDS)

        This allows graceful degradation when token endpoint is unreachable:
        use cached token instead of failing immediately.
        """
        if not self.access_token:
            return False

        if not self.expires_at:
            return True  # No expiry; assume valid

        # Normalise to naive UTC
        expiry = self.expires_at
        if expiry.tzinfo is not None:
            expiry = expiry.astimezone(timezone.utc).replace(tzinfo=None)

        now = utc_now()
        seconds_past_expiry = (now - expiry).total_seconds()

        # Token is within grace period if it hasn't expired past the grace window
        return seconds_past_expiry < _GRACE_PERIOD_SECONDS

    def get_token_info(self) -> Dict[str, Any]:
        """Return a safe summary of the current token state."""
        token_preview = mask_secret(self.access_token) if self.access_token else "None"
        return {
            "access_token": token_preview,
            "has_refresh_token": bool(self.refresh_token),
            "expires_at": self._expires_at_iso(),
            "needs_refresh": self._needs_refresh(),
        }

    def _validate_token_from_endpoint(self, raw_token: str, token_type: str) -> str:
        """Validate and return a token received from the endpoint.

        Prevents CRLF injection and validates token format from untrusted source.

        Args:
            raw_token: The raw token value from the token endpoint.
            token_type: Token type for error messages (e.g., "access_token", "refresh_token").

        Returns:
            The validated token string.

        Raises:
            AuthError: If token validation fails (injection attempt, invalid format, etc).
        """
        label = f"OAuth2 {token_type} (from endpoint)"
        return _validate_credential(raw_token, label)

    # ── Secure storage ────────────────────────────────────────────────────────

    def _load_from_storage(self) -> None:
        """Restore tokens from secure storage, if available."""
        if not self._has_storage:
            logger.debug("OAuth2 secure storage not configured (key=%s)", self.storage_key)
            return

        try:
            logger.debug("Loading OAuth2 tokens from secure storage (key=%s)", self.storage_key)
            stored = self.secure_storage.retrieve(self.storage_key)
            if stored:
                try:
                    data = json.loads(stored)
                except (json.JSONDecodeError, ValueError) as parse_exc:
                    logger.warning(
                        "Failed to parse stored OAuth2 tokens (corrupted data): %s",
                        parse_exc
                    )
                    return

                self.access_token = data.get("access_token")
                self.refresh_token = data.get("refresh_token")
                self.expires_at = self._parse_expires_at(data.get("expires_at"))
                logger.info(
                    "OAuth2 tokens loaded from storage (expires_at=%s)",
                    self._expires_at_iso() or "None",
                )
            else:
                logger.debug("No stored OAuth2 tokens found for key=%s", self.storage_key)
        except (OSError, IOError) as io_exc:
            logger.warning("Failed to access secure storage (I/O error): %s", io_exc)
            self._storage_available = False
        except Exception as storage_exc:
            logger.error("Unexpected error loading OAuth2 tokens: %s", storage_exc, exc_info=True)
            self._storage_available = False

    def _save_to_storage(self) -> None:
        """Persist current tokens to secure storage."""
        if not self._has_storage:
            logger.debug("OAuth2 secure storage not configured, skipping save")
            return

        try:
            logger.debug("Saving OAuth2 tokens to secure storage (key=%s)", self.storage_key)
            data = {
                "access_token": self.access_token,
                "refresh_token": self.refresh_token,
                "expires_at": self._expires_at_iso(),
            }
            try:
                json_str = json.dumps(data)
            except (TypeError, ValueError) as json_exc:
                logger.error("Failed to serialize OAuth2 tokens for storage: %s", json_exc)
                return

            self.secure_storage.store(self.storage_key, json_str)
            logger.info(
                "OAuth2 tokens saved to storage (expires_at=%s)",
                self._expires_at_iso() or "None",
            )
        except (OSError, IOError) as io_exc:
            logger.warning("Failed to write to secure storage (I/O error): %s", io_exc)
            self._storage_available = False
        except Exception as storage_exc:
            logger.error("Unexpected error saving OAuth2 tokens: %s", storage_exc, exc_info=True)
            self._storage_available = False

    def _expires_at_iso(self) -> Optional[str]:
        """Return ``expires_at`` as an ISO-8601 string, or ``None`` if unset."""
        return self.expires_at.isoformat() if self.expires_at else None

    @staticmethod
    def _parse_expires_at(expires_at_str: Optional[str]) -> Optional[datetime]:
        """Parse an ISO-8601 expiry string into a naive UTC datetime."""
        if not expires_at_str:
            return None
        try:
            parsed = datetime.fromisoformat(expires_at_str)
            return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _parse_expires_in(raw_expires: Any) -> int:
        """Return a valid ``expires_in`` seconds value from a raw token response field.

        Accepts int, float, or string representations.  Falls back to
        ``_DEFAULT_TOKEN_EXPIRY_SECONDS`` when the value is absent, zero,
        negative, or cannot be parsed.

        Args:
            raw_expires: The raw ``expires_in`` value from the token endpoint.
        """
        try:
            value = int(float(raw_expires)) if raw_expires is not None else 0
            if value > 0:
                return value
        except (ValueError, TypeError):
            logger.warning(
                "Invalid expires_in value %r, using default %ds",
                raw_expires, _DEFAULT_TOKEN_EXPIRY_SECONDS,
            )
        return _DEFAULT_TOKEN_EXPIRY_SECONDS

    # ── Token refresh ─────────────────────────────────────────────────────────

    def _refresh_access_token(
        self,
        *,
        proxy: Optional[str] = None,
        verify_ssl: Optional[bool] = None,
    ) -> None:
        """Fetch a new access token using the refresh-token or client-credentials flow.

        Gracefully falls back to cached token if endpoint is unreachable and token
        is still within the grace period (within 30 seconds of expiry).

        Raises:
            AuthError: If no token URL is configured or the endpoint rejects the request.
        """
        if not self.token_url:
            raise AuthError("No token URL configured for token refresh")

        grant_data = self._build_grant_data()
        logger.debug(
            "Initiating OAuth2 token refresh",
            extra={
                "token_url": redact_url(self.token_url),
                "grant_type": grant_data.get("grant_type"),
                "client_id": self.client_id or "anonymous",
                "proxy": proxy or "default",
                "verify_ssl": verify_ssl if verify_ssl is not None else "default",
            },
        )
        try:
            response = self._post_token_request(
                grant_data,
                proxy=proxy,
                verify_ssl=verify_ssl,
            )
            self._capture_token_response(response)
            self._apply_token_response(response)
            logger.info(
                "OAuth2 token successfully refreshed",
                extra={
                    "token_url": redact_url(self.token_url),
                    "expires_at": self.expires_at.isoformat() if self.expires_at else None,
                },
            )
        except AuthError as refresh_error:
            # Graceful degradation: if token endpoint is unreachable but cached token
            # is still within grace period, use it instead of failing immediately
            if self._is_token_within_grace_period():
                logger.warning(
                    "Token endpoint unreachable (error: %s), but cached token still valid; "
                    "proceeding with existing token (grace period: %ds)",
                    str(refresh_error),
                    _GRACE_PERIOD_SECONDS,
                    extra={
                        "token_url": redact_url(self.token_url),
                        "error_type": type(refresh_error).__name__,
                        "grace_period_seconds": _GRACE_PERIOD_SECONDS,
                        "cached_token_expiry": self.expires_at.isoformat() if self.expires_at else None,
                    },
                )
                self._audit.log_event(
                    AuditEventType.AUTH_TOKEN_REFRESH,
                    level=AuditLevel.INFO,
                    message="Using cached OAuth2 token within grace period due to endpoint failure"
                )
                return
            # Token is expired or missing; let the error propagate
            logger.error(
                "OAuth2 token refresh failed and no valid cached token available",
                extra={
                    "token_url": redact_url(self.token_url),
                    "error": str(refresh_error),
                    "error_details": getattr(refresh_error, "details", {}),
                },
            )
            raise

    @staticmethod
    def _snapshot_response_body(response: httpx.Response) -> Dict[str, Any]:
        """Return a redacted snapshot of the token response body.

        Tries to parse as JSON first; falls back to a truncated raw text
        representation; returns an empty dict when both fail.
        """
        try:
            redacted = {k: _redact_token_value(k, v) for k, v in response.json().items()}
            return sanitize_details(redacted)
        except Exception:
            pass
        try:
            raw = response.text
            return sanitize_details({"_raw": raw[:_TOKEN_RESPONSE_RAW_MAX] if raw else ""})
        except Exception:
            return {}

    def _capture_token_response(self, response: httpx.Response) -> None:
        """Store a redacted snapshot of the token endpoint response for inspection."""
        try:
            resp_headers = {
                k: v for k, v in response.headers.items()
                if k.lower() not in _FILTERED_RESPONSE_HEADERS
            }
        except Exception:
            resp_headers = {}

        try:
            raw_url = str(response.request.url) if response.request else self.token_url
            url = redact_url(raw_url) if raw_url else ""
        except Exception:
            url = redact_url(self.token_url) if self.token_url else ""

        try:
            status = response.status_code
        except Exception:
            status = 0

        self._last_token_response = {
            "status_code": status,
            "headers": resp_headers,
            "body": self._snapshot_response_body(response),
            "url": url,
            "method": "POST",
        }

    def _refresh_token_grant_data(self) -> Dict[str, Any]:
        """Build grant data for the refresh-token flow (RFC 6749 §6)."""
        data: Dict[str, Any] = {
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token,
        }
        if self.client_id:
            data["client_id"] = self.client_id
        if self.client_secret:
            data["client_secret"] = self.client_secret
        return data

    def _client_credentials_grant_data(self) -> Dict[str, Any]:
        """Build grant data for the client-credentials flow (RFC 6749 §4.4)."""
        return {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }

    def _build_grant_data(self) -> Dict[str, Any]:
        """Build the form-data payload for the token endpoint.

        Prefers refresh-token flow; falls back to client-credentials.

        Raises:
            AuthError: If neither flow is possible.
        """
        if self.refresh_token:
            logger.debug("Refreshing OAuth2 token using refresh token")
            data = self._refresh_token_grant_data()
        elif self.client_id and self.client_secret:
            logger.debug("Refreshing OAuth2 token using client credentials")
            data = self._client_credentials_grant_data()
        else:
            raise AuthError("No refresh token or client credentials configured")

        # RFC 6749 §6: scope is OPTIONAL on refresh, but many servers honour it.
        if self.scope:
            data["scope"] = self.scope
        # Include any extra params for token endpoint requests
        if self.extra_params:
            data.update(self.extra_params)
        return data

    def _make_token_timeout(self) -> httpx.Timeout:
        """Return the httpx.Timeout for token endpoint requests.

        The connect timeout is capped independently so that a dead proxy fails
        fast at TCP level without waiting the full ``token_timeout``.
        """
        return httpx.Timeout(
            connect=min(self.token_timeout, _MAX_CONNECT_TIMEOUT),
            read=self.token_timeout,
            write=self.token_timeout,
            pool=self.token_timeout,
        )

    def _make_basic_auth_header(self) -> str:
        """Return the Basic auth header value, delegating to module-level helper.

        Raises:
            AuthError: If client_id or client_secret is missing.
        """
        client_id = self.client_id or ""
        client_secret = self.client_secret or ""
        return make_oauth2_basic_auth_header(client_id, client_secret)

    def _execute_token_post(
        self,
        grant_data: Dict[str, Any],
        *,
        proxy: Optional[str] = None,
        verify_ssl: Optional[bool] = None,
    ) -> httpx.Response:
        """Perform a single HTTP POST to the token endpoint.

        Sends credentials either in the request body or as an Authorization header
        (HTTP Basic Auth), depending on the configured ``token_auth`` mode.

        Raises:
            httpx.HTTPStatusError: On a non-2xx response.
            httpx.TransportError, httpx.TimeoutException: On network failure.
        """
        assert self.token_url is not None  # guaranteed by _refresh_access_token guard

        headers: Dict[str, str] = {}
        body = grant_data.copy()

        if self.token_auth == "basic":
            # D&B Direct+ / RFC 6749 §2.3.1: credentials in Authorization header
            headers["Authorization"] = self._make_basic_auth_header()
            # Remove client credentials from body when using Basic auth
            body.pop("client_id", None)
            body.pop("client_secret", None)

        with httpx.Client(
            timeout=self._make_token_timeout(),
            proxy=proxy if proxy is not None else self._proxy_for_httpx,
            verify=self._verify_ssl if verify_ssl is None else bool(verify_ssl),
        ) as client:
            response = client.post(self.token_url, data=body, headers=headers)
        response.raise_for_status()
        return response

    def _post_token_request(
        self,
        grant_data: Dict[str, Any],
        *,
        proxy: Optional[str] = None,
        verify_ssl: Optional[bool] = None,
    ) -> httpx.Response:
        """POST grant_data to the token endpoint with retry on transient network errors.

        4xx responses are not retried — they signal bad credentials.

        Raises:
            AuthError: After exhausting retries or on HTTP error status.
        """
        assert self.token_url is not None  # guaranteed by _refresh_access_token guard

        # Validate token URL with full structural + SSRF checks (scheme,
        # private-IP, metadata-endpoint blocking).  At send-time the URL is
        # fully resolved so validate_resolved_url is appropriate.
        try:
            Validator.validate_resolved_url(self.token_url)
        except Exception as exc:
            raise AuthError(f"Invalid token URL: {exc}", details={"token_url": self.token_url})

        last_exc: Optional[Exception] = None
        attempts_made = 0

        for attempt in range(_MAX_TOKEN_RETRIES):
            attempts_made = attempt + 1
            logger.debug(
                "Token request to %s (attempt %d/%d, proxy=%s, verify_ssl=%s)",
                redact_url(self.token_url),
                attempts_made,
                _MAX_TOKEN_RETRIES,
                proxy or self._proxy_label,
                verify_ssl if verify_ssl is not None else "default",
                extra={
                    "attempt": attempts_made,
                    "max_attempts": _MAX_TOKEN_RETRIES,
                    "token_url": redact_url(self.token_url),
                    "grant_type": grant_data.get("grant_type"),
                },
            )
            try:
                response = self._execute_token_post(
                    grant_data,
                    proxy=proxy,
                    verify_ssl=verify_ssl,
                )
                logger.info(
                    "Token request succeeded on attempt %d/%d",
                    attempts_made, _MAX_TOKEN_RETRIES,
                    extra={
                        "attempt": attempts_made,
                        "status_code": response.status_code,
                        "token_url": redact_url(self.token_url),
                    },
                )
                return response
            except httpx.HTTPStatusError as status_exc:
                status_code = "unknown"
                token_response: Optional[Dict[str, Any]] = None
                if status_exc.response is not None:
                    self._capture_token_response(status_exc.response)
                    status_code = status_exc.response.status_code
                    token_response = self._last_token_response
                response_preview = ""
                if status_exc.response is not None:
                    safe_snapshot = self._snapshot_response_body(status_exc.response)
                    response_preview = json.dumps(safe_snapshot, ensure_ascii=True, default=str)
                    if len(response_preview) > 400:
                        response_preview = response_preview[:400] + "..."
                    if token_response is None:
                        token_response = {
                            "status_code": status_code,
                            "headers": {},
                            "body": safe_snapshot,
                            "url": redact_url(self.token_url) if self.token_url else "",
                            "method": "POST",
                        }
                error_msg = f"Token endpoint returned HTTP {status_code}"
                if response_preview:
                    error_msg += f" (response={response_preview})"
                logger.error(
                    "%s for %s on attempt %d/%d",
                    error_msg,
                    redact_url(self.token_url),
                    attempts_made,
                    _MAX_TOKEN_RETRIES,
                    extra={
                        "attempt": attempts_made,
                        "status_code": status_code,
                        "token_url": redact_url(self.token_url),
                        "response": safe_snapshot if status_exc.response else {},
                        "grant_type": grant_data.get("grant_type"),
                    },
                )
                self._audit.log_auth_failure("oauth2", error_msg)
                raise AuthError(
                    error_msg,
                    details={
                        "token_url": self.token_url,
                        "token_response": token_response,
                    },
                )
            except (httpx.TransportError, httpx.TimeoutException) as transient_exc:
                last_exc = transient_exc
                # ECONNREFUSED / WinError 10061 — nothing is listening on that
                # port.  This is never transient; retrying only wastes time.
                if _is_connection_refused(transient_exc):
                    logger.warning(
                        "Token request: connection refused on attempt %d — skipping retries"
                        " (proxy=%s, url=%s): %s",
                        attempts_made,
                        proxy or self._proxy_label,
                        redact_url(self.token_url),
                        transient_exc,
                        extra={
                            "attempt": attempts_made,
                            "token_url": redact_url(self.token_url),
                            "error": str(transient_exc),
                            "error_type": type(transient_exc).__name__,
                            "connection_refused": True,
                        },
                    )
                    break
                is_final = attempt == _MAX_TOKEN_RETRIES - 1
                wait_seconds = _RETRY_BACKOFF_BASE ** attempt  # 1 s, 2 s, …
                if is_final:
                    logger.warning(
                        "Token request failed on final attempt %d/%d (proxy=%s, url=%s): %s",
                        attempts_made, _MAX_TOKEN_RETRIES,
                        proxy or self._proxy_label,
                        redact_url(self.token_url),
                        transient_exc,
                        extra={
                            "attempt": attempts_made,
                            "max_attempts": _MAX_TOKEN_RETRIES,
                            "token_url": redact_url(self.token_url),
                            "error": str(transient_exc),
                            "error_type": type(transient_exc).__name__,
                            "is_final": True,
                        },
                    )
                else:
                    logger.debug(
                        "Token request failed (attempt %d/%d), retrying in %ds (proxy=%s, url=%s): %s",
                        attempts_made, _MAX_TOKEN_RETRIES, wait_seconds,
                        proxy or self._proxy_label,
                        redact_url(self.token_url),
                        transient_exc,
                        extra={
                            "attempt": attempts_made,
                            "max_attempts": _MAX_TOKEN_RETRIES,
                            "token_url": redact_url(self.token_url),
                            "error": str(transient_exc),
                            "error_type": type(transient_exc).__name__,
                            "retry_wait_seconds": wait_seconds,
                        },
                    )
                    time.sleep(wait_seconds)

        raise AuthError(
            f"Failed to refresh OAuth2 token after {attempts_made} attempt(s): {last_exc}",
            details={"token_url": self.token_url},
        ) from last_exc

    def _apply_token_response(self, response: httpx.Response) -> None:
        """Update internal state from a successful token endpoint response.

        Raises:
            AuthError: If the response body is missing the access_token field.
        """
        try:
            token_data = response.json()
        except (json.JSONDecodeError, ValueError) as parse_exc:
            error_msg = (
                f"Token endpoint returned non-JSON response. "
                f"Status: {response.status_code}, "
                f"Content-Type: {response.headers.get('content-type', 'unknown')}"
            )
            logger.error(
                "%s — Parse error: %s",
                error_msg,
                parse_exc,
                extra={
                    "token_url": redact_url(self.token_url),
                    "status_code": response.status_code,
                    "content_type": response.headers.get("content-type"),
                    "parse_error": str(parse_exc),
                    "response_length": len(response.text) if response.text else 0,
                },
            )
            self._audit.log_auth_failure("oauth2", error_msg)
            raise AuthError(error_msg)

        raw_access = token_data.get("access_token")
        if not raw_access:
            logger.error(
                "Token endpoint response missing access_token field",
                extra={
                    "token_url": redact_url(self.token_url),
                    "status_code": response.status_code,
                    "response_keys": list(token_data.keys()),
                },
            )
            raise AuthError("Token endpoint did not return access_token")

        # Validate tokens from the (untrusted) token endpoint immediately
        # — prevents CRLF header injection and oversized payloads.
        self.access_token = self._validate_token_from_endpoint(raw_access, "access_token")

        expires_in_seconds = self._parse_expires_in(token_data.get("expires_in"))
        self.expires_at = utc_now() + timedelta(seconds=expires_in_seconds)
        logger.debug(
            "OAuth2 token will expire in %d seconds",
            expires_in_seconds,
            extra={
                "token_url": redact_url(self.token_url),
                "expires_in_seconds": expires_in_seconds,
                "expires_at": self.expires_at.isoformat(),
            },
        )

        if "refresh_token" in token_data:
            try:
                self.refresh_token = self._validate_token_from_endpoint(
                    token_data["refresh_token"], "refresh_token"
                )
            except AuthError:
                logger.warning(
                    "Token endpoint returned an invalid refresh_token — "
                    "keeping previous refresh_token"
                )

        self._save_to_storage()

        logger.info("OAuth2 token refreshed successfully")
        self._audit.log_event(
            AuditEventType.AUTH_TOKEN_REFRESH,
            level=AuditLevel.INFO,
            message=f"OAuth2 token refreshed for client_id={self.client_id}",
        )
