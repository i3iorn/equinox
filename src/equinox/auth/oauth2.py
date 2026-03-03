"""OAuth2 authentication with secure token storage and refresh"""

import json
import time
import logging
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Dict, Any, Optional

import httpx

from equinox.auth.base import AuthStrategy, _validate_credential
from equinox.core.exceptions import AuthError
from equinox.core.secure_storage import SecureStorage
from equinox.core.audit import get_audit_logger, AuditEventType, AuditLevel

logger = logging.getLogger(__name__)

_MAX_TOKEN_RETRIES = 3
_DEFAULT_TOKEN_EXPIRY_SECONDS = 3600  # Assume 1-hour lifetime when server omits expires_in


class OAuth2Auth(AuthStrategy):
    """OAuth2 authentication with token management and secure storage.

    Features:
    - Automatic token refresh before expiration
    - Secure token storage (AES-256 encrypted)
    - Support for refresh token and client credentials flows
    - Configurable refresh buffer (e.g., refresh 30s before expiry)
    """

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
        """
        self.access_token = access_token
        self.token_url = token_url
        self.client_id = client_id
        self.client_secret = client_secret
        self.scope = scope
        self.refresh_token = refresh_token
        self.expires_at: Optional[datetime] = None

        # Validate and clamp token_timeout
        if not isinstance(token_timeout, (int, float)) or token_timeout <= 0:
            token_timeout = self.DEFAULT_TOKEN_TIMEOUT
        self.token_timeout = max(0.1, min(token_timeout, 300.0))

        self.secure_storage = secure_storage
        # Prevent storage-key collision when client_id is None.
        if storage_key:
            self.storage_key = storage_key
        elif client_id:
            self.storage_key = f"oauth2_{client_id}"
        else:
            import uuid
            self.storage_key = f"oauth2_anonymous_{uuid.uuid4().hex[:12]}"

        # Prevent concurrent token-refresh races
        self._refresh_lock = Lock()
        self._audit = get_audit_logger()

        if self.secure_storage and self.storage_key:
            self._load_from_storage()

    # ── AuthStrategy interface ────────────────────────────────────────────────

    def apply(self, request: Any, headers: Dict[str, str]) -> None:
        """Add Authorization header with OAuth2 bearer token.

        Refreshes the token first when it is missing, expired, or expiring soon.
        Concurrent callers share the same refresh attempt via a lock.
        """
        with self._refresh_lock:
            if self._needs_refresh():
                self._refresh_access_token()

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
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any], secure_storage: Optional[SecureStorage] = None) -> "OAuth2Auth":
        """Create from dictionary"""
        instance = cls(
            access_token=data.get("access_token"),
            token_url=data.get("token_url"),
            client_id=data.get("client_id"),
            client_secret=data.get("client_secret"),
            scope=data.get("scope"),
            refresh_token=data.get("refresh_token"),
            secure_storage=secure_storage,
        )
        # Restore expiration so _needs_refresh() can make the right decision.
        instance.expires_at = cls._parse_expires_at(data.get("expires_at"))
        return instance

    def __repr__(self) -> str:
        token_status = "present" if self.access_token else "None"
        return (
            f"OAuth2Auth(client_id={self.client_id}, "
            f"access_token={token_status}, expires_at={self.expires_at})"
        )

    # ── Token state helpers ───────────────────────────────────────────────────

    def _needs_refresh(self) -> bool:
        """Return True when the token is missing, expired, or about to expire."""
        if not self.access_token:
            return True

        if not self.expires_at:
            # No expiration info — reuse until the server rejects with 401.
            # Forcing a refresh every request would exhaust client-credentials grants.
            return False

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        expiry = self.expires_at.replace(tzinfo=None) if self.expires_at.tzinfo else self.expires_at
        seconds_until_expiry = (expiry - now).total_seconds()
        return seconds_until_expiry <= self.REFRESH_BUFFER_SECONDS

    def get_token_info(self) -> Dict[str, Any]:
        """Return a safe summary of the current token state."""
        token_preview = (
            f"{self.access_token[:8]}..." if self.access_token and len(self.access_token) > 8
            else "None"
        )
        return {
            "access_token": token_preview,
            "has_refresh_token": bool(self.refresh_token),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "needs_refresh": self._needs_refresh(),
        }

    # ── Secure storage ────────────────────────────────────────────────────────

    def _load_from_storage(self) -> None:
        """Restore tokens from secure storage, if available."""
        if not self.secure_storage or not self.storage_key:
            return

        try:
            stored = self.secure_storage.retrieve(self.storage_key)
            if stored:
                data = json.loads(stored)
                self.access_token = data.get("access_token")
                self.refresh_token = data.get("refresh_token")
                self.expires_at = self._parse_expires_at(data.get("expires_at"))
                logger.info("OAuth2 tokens loaded from secure storage")
        except Exception as storage_exc:
            logger.warning("Failed to load OAuth2 tokens from storage: %s", storage_exc)

    def _save_to_storage(self) -> None:
        """Persist current tokens to secure storage."""
        if not self.secure_storage or not self.storage_key:
            return

        try:
            data = {
                "access_token": self.access_token,
                "refresh_token": self.refresh_token,
                "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            }
            self.secure_storage.store(self.storage_key, json.dumps(data))
            logger.info("OAuth2 tokens saved to secure storage")
        except Exception as storage_exc:
            logger.warning("Failed to save OAuth2 tokens to storage: %s", storage_exc)

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

    # ── Token refresh ─────────────────────────────────────────────────────────

    def _refresh_access_token(self) -> None:
        """Fetch a new access token using the refresh-token or client-credentials flow.

        Raises:
            AuthError: If no token URL is configured or the endpoint rejects the request.
        """
        if not self.token_url:
            raise AuthError("No token URL configured for token refresh")

        grant_data = self._build_grant_data()
        response = self._post_token_request(grant_data)
        self._apply_token_response(response)

    def _build_grant_data(self) -> Dict[str, Any]:
        """Build the form-data payload for the token endpoint.

        Prefers refresh-token flow; falls back to client-credentials.

        Raises:
            AuthError: If neither flow is possible.
        """
        if self.refresh_token:
            logger.debug("Refreshing OAuth2 token using refresh token")
            data: Dict[str, Any] = {
                "grant_type": "refresh_token",
                "refresh_token": self.refresh_token,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            }
            # RFC 6749 §6: scope is OPTIONAL on refresh, but many servers honour it.
            if self.scope:
                data["scope"] = self.scope
            return data

        if self.client_id and self.client_secret:
            logger.debug("Refreshing OAuth2 token using client credentials")
            data = {
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            }
            if self.scope:
                data["scope"] = self.scope
            return data

        raise AuthError("No refresh token or client credentials configured")

    def _post_token_request(self, grant_data: Dict[str, Any]) -> httpx.Response:
        """POST grant_data to the token endpoint with retry on transient network errors.

        4xx responses are not retried — they signal bad credentials.

        Raises:
            AuthError: After exhausting retries or on HTTP error status.
        """
        last_exc: Optional[Exception] = None

        # Validate token URL against SSRF before making the request
        from equinox.core.validation import Validator
        try:
            Validator.validate_url(self.token_url)
        except Exception as exc:
            raise AuthError(f"Invalid token URL: {exc}", details={"token_url": self.token_url})

        for attempt in range(_MAX_TOKEN_RETRIES):
            try:
                response = httpx.post(
                    self.token_url,
                    data=grant_data,
                    timeout=self.token_timeout,
                )
                response.raise_for_status()
                return response
            except httpx.HTTPStatusError as status_exc:
                error_msg = f"Token endpoint returned HTTP {status_exc.response.status_code}"
                logger.error(error_msg)
                self._audit.log_auth_failure("oauth2", error_msg)
                raise AuthError(error_msg, details={"token_url": self.token_url})
            except (httpx.TransportError, httpx.TimeoutException) as transient_exc:
                last_exc = transient_exc
                if attempt < _MAX_TOKEN_RETRIES - 1:
                    wait_seconds = 2 ** attempt  # 1s, 2s
                    logger.warning(
                        "Token request failed (attempt %d/%d), retrying in %ds: %s",
                        attempt + 1, _MAX_TOKEN_RETRIES, wait_seconds, transient_exc,
                    )
                    time.sleep(wait_seconds)

        raise AuthError(
            f"Failed to refresh OAuth2 token after {_MAX_TOKEN_RETRIES} attempts: {last_exc}",
            details={"token_url": self.token_url},
        ) from last_exc

    def _apply_token_response(self, response: httpx.Response) -> None:
        """Update internal state from a successful token endpoint response.

        Raises:
            AuthError: If the response body is missing the access_token field.
        """
        try:
            token_data = response.json()
        except ValueError as parse_exc:
            error_msg = f"Invalid token endpoint response: {parse_exc}"
            logger.error(error_msg)
            self._audit.log_auth_failure("oauth2", error_msg)
            raise AuthError(error_msg)

        self.access_token = token_data.get("access_token")
        if not self.access_token:
            raise AuthError("Token endpoint did not return access_token")

        # Parse expires_in robustly — servers may return int, float, or
        # string representations.  Fall back to the default on any error.
        raw_expires = token_data.get("expires_in")
        try:
            expires_in_seconds = int(float(raw_expires)) if raw_expires is not None else _DEFAULT_TOKEN_EXPIRY_SECONDS
            if expires_in_seconds <= 0:
                expires_in_seconds = _DEFAULT_TOKEN_EXPIRY_SECONDS
        except (ValueError, TypeError):
            logger.warning(
                "Invalid expires_in value %r, using default %ds",
                raw_expires, _DEFAULT_TOKEN_EXPIRY_SECONDS,
            )
            expires_in_seconds = _DEFAULT_TOKEN_EXPIRY_SECONDS

        self.expires_at = (
            datetime.now(timezone.utc).replace(tzinfo=None)
            + timedelta(seconds=expires_in_seconds)
        )
        logger.debug("OAuth2 token will expire in %d seconds", expires_in_seconds)

        if "refresh_token" in token_data:
            self.refresh_token = token_data["refresh_token"]

        self._save_to_storage()

        logger.info("OAuth2 token refreshed successfully")
        self._audit.log_event(
            AuditEventType.AUTH_TOKEN_REFRESH,
            level=AuditLevel.INFO,
            message=f"OAuth2 token refreshed for client_id={self.client_id}",
        )
