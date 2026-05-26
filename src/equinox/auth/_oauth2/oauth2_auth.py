"""OAuth2 authentication with secure token storage and refresh."""

# mypy: disable-error-code=attr-defined

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Literal

import httpx

from equinox.auth._base import AuthError, AuthStrategy, _interpolate_field, _validate_credential
from equinox.auth._oauth2.constants import (
    _DEFAULT_TOKEN_EXPIRY_SECONDS,
    _FILTERED_RESPONSE_HEADERS,
    _GRACE_PERIOD_SECONDS,
    _LOCK_TIMEOUT,
    _MAX_CONNECT_TIMEOUT,
    _TOKEN_RESPONSE_RAW_MAX,
)
from equinox.auth._oauth2.helpers import (
    credential_diagnostics,
    make_oauth2_basic_auth_header,
    redact_token_value,
    token_error_code,
)
from equinox.auth._oauth2.operations import (
    apply_post_token_request,
    apply_token_response,
    execute_token_post,
    initialize_oauth2_auth,
    refresh_access_token,
    try_alternate_client_auth_mode,
    try_client_credentials_fallback,
)
from equinox.core.util.time import utc_now
from equinox.security import mask_secret, redact_url, sanitize_details
from equinox.security.secure_storage import SecureStorage

logger = logging.getLogger(__name__)


class OAuth2Auth(AuthStrategy):
    """OAuth2 authentication with token management and secure storage."""

    AUTH_TYPE = "oauth2"
    DISPLAY_NAME = "OAuth 2.0"

    REFRESH_BUFFER_SECONDS = 30
    DEFAULT_TOKEN_TIMEOUT = 10.0

    def __init__(
        self,
        access_token: str | None = None,
        token_url: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        scope: str | None = None,
        refresh_token: str | None = None,
        secure_storage: SecureStorage | None = None,
        storage_key: str | None = None,
        token_timeout: float = 10.0,
        verify_ssl: bool = True,
        token_auth: Literal["body", "basic"] = "body",
        extra_params: dict[str, Any] | None = None,
    ):
        """Initialize OAuth2 auth with optional secure storage."""
        initialize_oauth2_auth(
            self,
            access_token=access_token,
            token_url=token_url,
            client_id=client_id,
            client_secret=client_secret,
            scope=scope,
            refresh_token=refresh_token,
            secure_storage=secure_storage,
            storage_key=storage_key,
            token_timeout=token_timeout,
            verify_ssl=verify_ssl,
            token_auth=token_auth,
            extra_params=extra_params,
        )

    def apply(self, request: Any, headers: dict[str, str]) -> None:
        """Add Authorization header with OAuth2 bearer token."""
        self.apply_with_context(request, headers, proxy=None, verify_ssl=None)

    def apply_with_context(
        self,
        request: Any,
        headers: dict[str, str],
        *,
        proxy: str | None = None,
        verify_ssl: bool | None = None,
    ) -> None:
        """Apply auth using optional runtime transport context."""
        effective_proxy = proxy if proxy is not None else self._proxy
        effective_verify_ssl = bool(verify_ssl) if verify_ssl is not None else self._verify_ssl

        acquired = self._refresh_lock.acquire(timeout=_LOCK_TIMEOUT)
        if not acquired:
            logger.error("Failed to acquire token refresh lock within %.1f seconds", _LOCK_TIMEOUT)
            raise AuthError(
                f"Token refresh lock timeout - possible deadlock or high contention "
                f"(waited {_LOCK_TIMEOUT}s)"
            )

        try:
            if self._needs_refresh():
                self._refresh_access_token(proxy=effective_proxy, verify_ssl=effective_verify_ssl)
        finally:
            self._refresh_lock.release()

        if not self.access_token:
            raise AuthError("No access token available")
        _validate_credential(self.access_token, "OAuth2 access token")
        headers["Authorization"] = f"Bearer {self.access_token}"

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary for storage."""
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
    def from_dict(cls, data: dict[str, Any], **kwargs: Any) -> "OAuth2Auth":
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
        instance.expires_at = cls._parse_expires_at(data.get("expires_at"))
        return instance

    def interpolate(self, interp: Callable[[str], str]) -> "OAuth2Auth":
        """Return a copy with expanded ``{{VAR}}`` placeholders."""
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
        new_auth.expires_at = self.expires_at
        return new_auth

    def get_display_summary(self) -> str:
        return f"Token URL: {self.token_url or '-'}\n" f"Client ID: {self.client_id or '-'}"

    def get_preflight_warning(self) -> str | None:
        if not self.token_url:
            return "OAuth2 token URL is not configured"
        return None

    def __repr__(self) -> str:
        token_status = "present" if self.access_token else "None"
        return (
            f"OAuth2Auth(client_id={self.client_id}, "
            f"access_token={token_status}, expires_at={self.expires_at})"
        )

    @property
    def last_token_response(self) -> dict[str, Any] | None:
        """The most recent (redacted) token endpoint exchange, or ``None``."""
        return self._last_token_response

    @property
    def _has_storage(self) -> bool:
        """Return True when secure storage is configured and a key is set."""
        return bool(self.secure_storage and self.storage_key)

    @property
    def storage_available(self) -> bool:
        """Return False if a secure-storage I/O error has been observed."""
        return self._storage_available

    @property
    def _proxy_label(self) -> str:
        """Human-readable proxy label used in log messages."""
        return self._proxy or "none"

    @property
    def _proxy_for_httpx(self) -> str | None:
        """Proxy URL passed to httpx, or ``None`` when no proxy is configured."""
        return self._proxy or None

    @property
    def verify_ssl(self) -> bool:
        """Whether TLS certificate verification is enabled for token requests."""
        return bool(self._verify_ssl)

    def _needs_refresh(self) -> bool:
        """Return True when token is missing, expired, or about to expire."""
        if not self.access_token:
            return True
        if not self.expires_at:
            return False

        expiry = self.expires_at
        if expiry.tzinfo is not None:
            expiry = expiry.astimezone(timezone.utc).replace(tzinfo=None)
        seconds_until_expiry = (expiry - utc_now()).total_seconds()
        return seconds_until_expiry <= self.REFRESH_BUFFER_SECONDS

    def _is_token_within_grace_period(self) -> bool:
        """Return True when cached token remains usable inside grace window."""
        if not self.access_token:
            return False
        if not self.expires_at:
            return True

        expiry = self.expires_at
        if expiry.tzinfo is not None:
            expiry = expiry.astimezone(timezone.utc).replace(tzinfo=None)
        seconds_past_expiry = (utc_now() - expiry).total_seconds()
        return seconds_past_expiry < _GRACE_PERIOD_SECONDS

    def get_token_info(self) -> dict[str, Any]:
        """Return a safe summary of the current token state."""
        token_preview = mask_secret(self.access_token) if self.access_token else "None"
        return {
            "access_token": token_preview,
            "has_refresh_token": bool(self.refresh_token),
            "expires_at": self._expires_at_iso(),
            "needs_refresh": self._needs_refresh(),
        }

    def _validate_token_from_endpoint(self, raw_token: str, token_type: str) -> str:
        """Validate and return a token received from the endpoint."""
        label = f"OAuth2 {token_type} (from endpoint)"
        return _validate_credential(raw_token, label)

    def _load_from_storage(self) -> None:
        """Restore tokens from secure storage, if available."""
        if not self._has_storage:
            logger.debug("OAuth2 secure storage not configured (key=%s)", self.storage_key)
            return
        if self.secure_storage is None:
            return

        try:
            logger.debug("Loading OAuth2 tokens from secure storage (key=%s)", self.storage_key)
            stored = self.secure_storage.retrieve(self.storage_key)
            if not stored:
                logger.debug("No stored OAuth2 tokens found for key=%s", self.storage_key)
                return

            try:
                data = json.loads(stored)
            except (json.JSONDecodeError, ValueError) as parse_exc:
                logger.warning(
                    "Failed to parse stored OAuth2 tokens (corrupted data): %s", parse_exc
                )
                return

            self.access_token = data.get("access_token")
            self.refresh_token = data.get("refresh_token")
            self.expires_at = self._parse_expires_at(data.get("expires_at"))
            logger.info("OAuth2 tokens loaded from storage (expires_at=%s)", self._expires_at_iso())
        except OSError as io_exc:
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
        if self.secure_storage is None:
            return

        try:
            logger.debug("Saving OAuth2 tokens to secure storage (key=%s)", self.storage_key)
            data = {
                "access_token": self.access_token,
                "refresh_token": self.refresh_token,
                "expires_at": self._expires_at_iso(),
            }
            json_str = json.dumps(data)
            self.secure_storage.store(self.storage_key, json_str)
            logger.info("OAuth2 tokens saved to storage (expires_at=%s)", self._expires_at_iso())
        except (TypeError, ValueError) as json_exc:
            logger.error("Failed to serialize OAuth2 tokens for storage: %s", json_exc)
        except OSError as io_exc:
            logger.warning("Failed to write to secure storage (I/O error): %s", io_exc)
            self._storage_available = False
        except Exception as storage_exc:
            logger.error("Unexpected error saving OAuth2 tokens: %s", storage_exc, exc_info=True)
            self._storage_available = False

    def _expires_at_iso(self) -> str | None:
        """Return ``expires_at`` as an ISO-8601 string, or ``None`` if unset."""
        return self.expires_at.isoformat() if self.expires_at else None

    @staticmethod
    def _parse_expires_at(expires_at_str: str | None) -> datetime | None:
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
        """Return a valid ``expires_in`` seconds value from token response."""
        try:
            value = int(float(raw_expires)) if raw_expires is not None else 0
            if value > 0:
                return value
        except (ValueError, TypeError):
            logger.warning(
                "Invalid expires_in value %r, using default %ds",
                raw_expires,
                _DEFAULT_TOKEN_EXPIRY_SECONDS,
            )
        return _DEFAULT_TOKEN_EXPIRY_SECONDS

    def _refresh_access_token(
        self,
        *,
        proxy: str | None = None,
        verify_ssl: bool | None = None,
    ) -> None:
        """Fetch a new access token from the OAuth2 token endpoint."""
        refresh_access_token(self, proxy=proxy, verify_ssl=verify_ssl)

    @staticmethod
    def _snapshot_response_body(response: httpx.Response) -> dict[str, Any]:
        """Return a redacted snapshot of the token response body."""
        try:
            redacted = {k: redact_token_value(k, v) for k, v in response.json().items()}
            return sanitize_details(redacted)
        except Exception:
            pass
        try:
            raw = response.text
            return sanitize_details({"_raw": raw[:_TOKEN_RESPONSE_RAW_MAX] if raw else ""})
        except Exception:
            return {}

    def _capture_token_response(self, response: httpx.Response) -> None:
        """Store a redacted snapshot of the token endpoint response."""
        try:
            resp_headers = {
                k: v
                for k, v in response.headers.items()
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

    def _refresh_token_grant_data(self) -> dict[str, Any]:
        """Build grant data for refresh-token flow."""
        data: dict[str, Any] = {
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token,
        }
        if self.client_id:
            data["client_id"] = self.client_id
        if self.client_secret:
            data["client_secret"] = self.client_secret
        return data

    def _client_credentials_grant_data(self) -> dict[str, Any]:
        """Build grant data for client-credentials flow."""
        return {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }

    def _build_grant_data(self) -> dict[str, Any]:
        """Build form-data payload for the token endpoint."""
        if self.refresh_token:
            logger.debug("Refreshing OAuth2 token using refresh token")
            data = self._refresh_token_grant_data()
        elif self.client_id and self.client_secret:
            logger.debug("Refreshing OAuth2 token using client credentials")
            data = self._client_credentials_grant_data()
        else:
            raise AuthError("No refresh token or client credentials configured")

        if self.scope:
            data["scope"] = self.scope
        if self.extra_params:
            data.update(self.extra_params)
        return data

    def _make_token_timeout(self) -> httpx.Timeout:
        """Return the httpx timeout used for token endpoint requests."""
        return httpx.Timeout(
            connect=min(self.token_timeout, _MAX_CONNECT_TIMEOUT),
            read=self.token_timeout,
            write=self.token_timeout,
            pool=self.token_timeout,
        )

    def _make_basic_auth_header(self) -> str:
        """Return Basic auth header value for token endpoint auth."""
        return make_oauth2_basic_auth_header(self.client_id or "", self.client_secret or "")

    @staticmethod
    def _credential_diagnostics(value: str | None) -> dict[str, Any]:
        """Return non-sensitive diagnostics about a credential string."""
        return credential_diagnostics(value)

    def _execute_token_post(
        self,
        grant_data: dict[str, Any],
        *,
        proxy: str | None = None,
        verify_ssl: bool | None = None,
    ) -> httpx.Response:
        """Perform a single HTTP POST to the token endpoint."""
        return execute_token_post(self, grant_data, proxy=proxy, verify_ssl=verify_ssl)

    @staticmethod
    def _token_error_code(response: httpx.Response | None) -> str:
        """Return OAuth2 token error code from response JSON, or empty string."""
        return token_error_code(response)

    def _try_alternate_client_auth_mode(
        self,
        grant_data: dict[str, Any],
        status_exc: httpx.HTTPStatusError,
        *,
        proxy: str | None = None,
        verify_ssl: bool | None = None,
    ) -> httpx.Response | None:
        """Retry once with alternate client-auth mode for invalid_client responses."""
        return try_alternate_client_auth_mode(
            self,
            grant_data,
            status_exc,
            proxy=proxy,
            verify_ssl=verify_ssl,
        )

    def _try_client_credentials_fallback(
        self,
        grant_data: dict[str, Any],
        status_exc: httpx.HTTPStatusError,
        *,
        proxy: str | None = None,
        verify_ssl: bool | None = None,
    ) -> httpx.Response | None:
        """Retry once with client_credentials when refresh_token grant is rejected."""
        return try_client_credentials_fallback(
            self,
            grant_data,
            status_exc,
            proxy=proxy,
            verify_ssl=verify_ssl,
        )

    def _post_token_request(
        self,
        grant_data: dict[str, Any],
        *,
        proxy: str | None = None,
        verify_ssl: bool | None = None,
    ) -> httpx.Response:
        """POST grant_data to token endpoint with retry on transient errors."""
        return apply_post_token_request(self, grant_data, proxy=proxy, verify_ssl=verify_ssl)

    def _apply_token_response(self, response: httpx.Response) -> None:
        """Update internal state from a successful token endpoint response."""
        apply_token_response(self, response)
