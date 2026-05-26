"""Internal OAuth2Auth operations extracted from the main class."""

from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import timedelta
from threading import Lock
from typing import Any, Literal, cast

import httpx

from equinox.auth._base import AuthError
from equinox.auth._oauth2.constants import (
    _ANON_KEY_ID_LENGTH,
    _GRACE_PERIOD_SECONDS,
    _MAX_CONNECT_TIMEOUT,
    _MAX_TOKEN_RETRIES,
    _REFRESH_GRANT_FALLBACK_ERRORS,
    _RETRY_BACKOFF_BASE,
)
from equinox.auth._oauth2.helpers import (
    credential_diagnostics,
    is_connection_refused,
    make_oauth2_basic_auth_header,
    token_error_code,
)
from equinox.core.audit import AuditEventType, AuditLevel, get_audit_logger
from equinox.core.util.time import utc_now
from equinox.core.validation import Validator
from equinox.security import redact_url

logger = logging.getLogger(__name__)


def initialize_oauth2_auth(
    auth: Any,
    *,
    access_token: str | None,
    token_url: str | None,
    client_id: str | None,
    client_secret: str | None,
    scope: str | None,
    refresh_token: str | None,
    secure_storage: Any,
    storage_key: str | None,
    token_timeout: float,
    verify_ssl: bool,
    token_auth: Literal["body", "basic"],
    extra_params: dict[str, Any] | None,
) -> None:
    """Initialize OAuth2Auth state and storage-backed token metadata."""
    auth.access_token = access_token
    auth.token_url = token_url
    auth.client_id = client_id
    auth.client_secret = client_secret
    auth.scope = scope
    auth.refresh_token = refresh_token
    auth.extra_params = extra_params or {}
    auth.expires_at = None

    _validate_token_auth(token_auth)
    auth.token_auth = token_auth

    auth.token_timeout = _validated_timeout(token_timeout, auth.DEFAULT_TOKEN_TIMEOUT)
    auth._verify_ssl = bool(verify_ssl)
    auth.secure_storage = secure_storage
    auth.storage_key = _resolve_storage_key(storage_key, client_id)

    auth._storage_available = True
    auth._refresh_lock = Lock()
    auth._audit = get_audit_logger()
    auth._proxy = None
    auth._last_token_response = None

    if auth.secure_storage and auth.storage_key:
        auth._load_from_storage()


def _validate_token_auth(token_auth: Literal["body", "basic"]) -> None:
    if token_auth not in ("body", "basic"):
        raise AuthError(f"Invalid token_auth {token_auth!r}. Must be 'body' or 'basic'.")


def _validated_timeout(raw_timeout: float, default_timeout: float) -> float:
    token_timeout = raw_timeout
    if not isinstance(token_timeout, (int, float)) or token_timeout <= 0:
        logger.warning(
            "Invalid token_timeout %r, using default %s seconds",
            token_timeout,
            default_timeout,
        )
        token_timeout = default_timeout
    clamped = max(0.1, min(token_timeout, 300.0))
    if clamped != token_timeout:
        logger.debug("Clamped token_timeout from %s to %s seconds", token_timeout, clamped)
    return clamped


def _resolve_storage_key(storage_key: str | None, client_id: str | None) -> str:
    if storage_key:
        return storage_key
    if client_id:
        return f"oauth2_{client_id}"
    return f"oauth2_anonymous_{uuid.uuid4().hex[:_ANON_KEY_ID_LENGTH]}"


def refresh_access_token(
    auth: Any,
    *,
    proxy: str | None = None,
    verify_ssl: bool | None = None,
) -> None:
    """Refresh token state and fall back to cached token during grace period."""
    if not auth.token_url:
        raise AuthError("No token URL configured for token refresh")

    grant_data = auth._build_grant_data()
    _log_refresh_start(auth, grant_data, proxy=proxy, verify_ssl=verify_ssl)

    try:
        response = auth._post_token_request(grant_data, proxy=proxy, verify_ssl=verify_ssl)
        auth._capture_token_response(response)
        auth._apply_token_response(response)
        _log_refresh_success(auth)
    except AuthError as refresh_error:
        if _use_cached_token_within_grace_period(auth, refresh_error):
            return
        _log_refresh_failure(auth, refresh_error)
        raise


def _log_refresh_start(
    auth: Any,
    grant_data: dict[str, Any],
    *,
    proxy: str | None,
    verify_ssl: bool | None,
) -> None:
    logger.debug(
        "Initiating OAuth2 token refresh",
        extra={
            "token_url": redact_url(auth.token_url),
            "grant_type": grant_data.get("grant_type"),
            "client_id": auth.client_id or "anonymous",
            "proxy": proxy or "default",
            "verify_ssl": verify_ssl if verify_ssl is not None else "default",
        },
    )


def _log_refresh_success(auth: Any) -> None:
    logger.info(
        "OAuth2 token successfully refreshed",
        extra={
            "token_url": redact_url(auth.token_url),
            "expires_at": auth.expires_at.isoformat() if auth.expires_at else None,
        },
    )


def _use_cached_token_within_grace_period(auth: Any, refresh_error: AuthError) -> bool:
    if not auth._is_token_within_grace_period():
        return False
    logger.warning(
        "Token endpoint unreachable (error: %s), using cached token within grace period (%ds)",
        str(refresh_error),
        _GRACE_PERIOD_SECONDS,
        extra={
            "token_url": redact_url(auth.token_url),
            "error_type": type(refresh_error).__name__,
            "grace_period_seconds": _GRACE_PERIOD_SECONDS,
            "cached_token_expiry": auth.expires_at.isoformat() if auth.expires_at else None,
        },
    )
    auth._audit.log_event(
        AuditEventType.AUTH_TOKEN_REFRESH,
        level=AuditLevel.INFO,
        message="Using cached OAuth2 token within grace period due to endpoint failure",
    )
    return True


def _log_refresh_failure(auth: Any, refresh_error: AuthError) -> None:
    logger.error(
        "OAuth2 token refresh failed and no valid cached token available",
        extra={
            "token_url": redact_url(auth.token_url),
            "error": str(refresh_error),
            "error_details": getattr(refresh_error, "details", {}),
        },
    )


def execute_token_post(
    auth: Any,
    grant_data: dict[str, Any],
    *,
    proxy: str | None = None,
    verify_ssl: bool | None = None,
) -> httpx.Response:
    """Execute one POST against the token endpoint."""
    assert auth.token_url is not None
    headers, body = _build_token_post_payload(auth, grant_data)
    _log_token_request_diagnostics(auth, body, headers)

    with httpx.Client(
        timeout=_make_token_timeout(auth),
        proxy=proxy if proxy is not None else auth._proxy_for_httpx,
        verify=auth._verify_ssl if verify_ssl is None else bool(verify_ssl),
    ) as client:
        response = client.post(auth.token_url, data=body, headers=headers)
    response.raise_for_status()
    return response


def _build_token_post_payload(
    auth: Any, grant_data: dict[str, Any]
) -> tuple[dict[str, str], dict[str, Any]]:
    headers: dict[str, str] = {}
    body = grant_data.copy()
    if auth.token_auth == "basic":
        headers["Authorization"] = make_oauth2_basic_auth_header(
            auth.client_id or "",
            auth.client_secret or "",
        )
        body.pop("client_id", None)
        body.pop("client_secret", None)
    return headers, body


def _make_token_timeout(auth: Any) -> httpx.Timeout:
    return httpx.Timeout(
        connect=min(auth.token_timeout, _MAX_CONNECT_TIMEOUT),
        read=auth.token_timeout,
        write=auth.token_timeout,
        pool=auth.token_timeout,
    )


def _log_token_request_diagnostics(
    auth: Any,
    body: dict[str, Any],
    headers: dict[str, str],
) -> None:
    client_id_diag = credential_diagnostics(auth.client_id)
    client_secret_diag = credential_diagnostics(auth.client_secret)
    logger.debug(
        "OAuth2 token request diagnostics",
        extra={
            "token_url": redact_url(auth.token_url),
            "token_auth": auth.token_auth,
            "grant_type": body.get("grant_type"),
            "has_authorization_header": "Authorization" in headers,
            "body_has_client_id": "client_id" in body,
            "body_has_client_secret": "client_secret" in body,
            "client_id_present": client_id_diag["is_present"],
            "client_id_has_outer_whitespace": client_id_diag["has_outer_whitespace"],
            "client_secret_present": client_secret_diag["is_present"],
            "client_secret_has_outer_whitespace": client_secret_diag["has_outer_whitespace"],
        },
    )
    if client_id_diag["has_outer_whitespace"] or client_secret_diag["has_outer_whitespace"]:
        logger.warning(
            "OAuth2 credentials contain leading/trailing whitespace; endpoint may reject invalid_client",
            extra={
                "token_url": redact_url(auth.token_url),
                "token_auth": auth.token_auth,
            },
        )


def try_alternate_client_auth_mode(
    auth: Any,
    grant_data: dict[str, Any],
    status_exc: httpx.HTTPStatusError,
    *,
    proxy: str | None = None,
    verify_ssl: bool | None = None,
) -> httpx.Response | None:
    """Retry once with body/basic auth-mode fallback for invalid_client."""
    response = getattr(status_exc, "response", None)
    status_code = response.status_code if response is not None else None
    oauth_error = token_error_code(response)
    if status_code not in (400, 401) or oauth_error != "invalid_client":
        return None

    current_mode = auth.token_auth
    alternate_mode: Literal["body", "basic"] = "basic" if current_mode == "body" else "body"
    if alternate_mode == "basic" and not (auth.client_id and auth.client_secret):
        return None

    auth.token_auth = alternate_mode
    logger.warning(
        "Token endpoint invalid_client with token_auth=%s; retrying with token_auth=%s",
        current_mode,
        alternate_mode,
        extra={"token_url": redact_url(auth.token_url)},
    )
    return _run_auth_mode_fallback(
        auth, grant_data, current_mode, alternate_mode, proxy, verify_ssl
    )


def _run_auth_mode_fallback(
    auth: Any,
    grant_data: dict[str, Any],
    current_mode: str,
    alternate_mode: str,
    proxy: str | None,
    verify_ssl: bool | None,
) -> httpx.Response | None:
    try:
        response = cast(
            httpx.Response,
            auth._execute_token_post(grant_data, proxy=proxy, verify_ssl=verify_ssl),
        )
        logger.info(
            "Token request succeeded after auth-mode fallback",
            extra={"token_url": redact_url(auth.token_url), "token_auth": alternate_mode},
        )
        return response
    except httpx.HTTPStatusError as fallback_exc:
        auth.token_auth = current_mode
        fallback_response = getattr(fallback_exc, "response", None)
        logger.warning(
            "Token endpoint fallback with token_auth=%s also failed (status=%s)",
            alternate_mode,
            fallback_response.status_code if fallback_response is not None else "unknown",
            extra={"token_url": redact_url(auth.token_url)},
        )
        return None
    except Exception:
        auth.token_auth = current_mode
        raise


def try_client_credentials_fallback(
    auth: Any,
    grant_data: dict[str, Any],
    status_exc: httpx.HTTPStatusError,
    *,
    proxy: str | None = None,
    verify_ssl: bool | None = None,
) -> httpx.Response | None:
    """Retry with client_credentials when refresh-token grant is unsupported."""
    if grant_data.get("grant_type") != "refresh_token":
        return None
    if not (auth.client_id and auth.client_secret):
        return None

    response = getattr(status_exc, "response", None)
    status_code = response.status_code if response is not None else None
    oauth_error = token_error_code(response)
    if status_code not in (400, 401) or oauth_error not in _REFRESH_GRANT_FALLBACK_ERRORS:
        return None

    fallback_grant = auth._client_credentials_grant_data()
    if auth.scope:
        fallback_grant["scope"] = auth.scope
    if auth.extra_params:
        fallback_grant.update(auth.extra_params)

    logger.warning(
        "Token endpoint rejected refresh_token grant (%s); retrying with client_credentials",
        oauth_error,
        extra={"token_url": redact_url(auth.token_url), "status_code": status_code},
    )
    return cast(
        httpx.Response,
        auth._execute_token_post(fallback_grant, proxy=proxy, verify_ssl=verify_ssl),
    )


def post_token_request(
    auth: Any,
    grant_data: dict[str, Any],
    *,
    proxy: str | None = None,
    verify_ssl: bool | None = None,
) -> httpx.Response:
    """Post grant data with retry handling and OAuth2-aware fallbacks."""
    assert auth.token_url is not None
    _validate_token_url(auth.token_url)

    last_exc: Exception | None = None
    attempts_made = 0
    for attempt in range(_MAX_TOKEN_RETRIES):
        attempts_made = attempt + 1
        _log_attempt_start(auth, grant_data, attempts_made, proxy, verify_ssl)
        try:
            return _attempt_token_request(auth, grant_data, attempts_made, proxy, verify_ssl)
        except httpx.HTTPStatusError as status_exc:
            _raise_http_status_auth_error(
                auth, grant_data, status_exc, proxy, verify_ssl, attempts_made
            )
        except (httpx.TransportError, httpx.TimeoutException) as transient_exc:
            last_exc = transient_exc
            if _handle_transport_failure(auth, transient_exc, attempt, attempts_made, proxy):
                break

    raise AuthError(
        f"Failed to refresh OAuth2 token after {attempts_made} attempt(s): {last_exc}",
        details={"token_url": auth.token_url},
    ) from last_exc


def _validate_token_url(token_url: str) -> None:
    try:
        Validator.validate_resolved_url(token_url)
    except Exception as exc:
        raise AuthError(f"Invalid token URL: {exc}", details={"token_url": token_url}) from exc


def _log_attempt_start(
    auth: Any,
    grant_data: dict[str, Any],
    attempts_made: int,
    proxy: str | None,
    verify_ssl: bool | None,
) -> None:
    logger.debug(
        "Token request to %s (attempt %d/%d, proxy=%s, verify_ssl=%s)",
        redact_url(auth.token_url),
        attempts_made,
        _MAX_TOKEN_RETRIES,
        proxy or auth._proxy_label,
        verify_ssl if verify_ssl is not None else "default",
        extra={"attempt": attempts_made, "token_url": redact_url(auth.token_url)},
    )


def _attempt_token_request(
    auth: Any,
    grant_data: dict[str, Any],
    attempts_made: int,
    proxy: str | None,
    verify_ssl: bool | None,
) -> httpx.Response:
    response = cast(
        httpx.Response,
        auth._execute_token_post(grant_data, proxy=proxy, verify_ssl=verify_ssl),
    )
    logger.info(
        "Token request succeeded on attempt %d/%d",
        attempts_made,
        _MAX_TOKEN_RETRIES,
        extra={"attempt": attempts_made, "status_code": response.status_code},
    )
    return response


def _raise_http_status_auth_error(
    auth: Any,
    grant_data: dict[str, Any],
    status_exc: httpx.HTTPStatusError,
    proxy: str | None,
    verify_ssl: bool | None,
    attempts_made: int,
) -> None:
    fallback_response = auth._try_alternate_client_auth_mode(
        grant_data,
        status_exc,
        proxy=proxy,
        verify_ssl=verify_ssl,
    )
    if fallback_response is not None:
        raise _ReturnResponse(fallback_response)

    grant_fallback_response = auth._try_client_credentials_fallback(
        grant_data,
        status_exc,
        proxy=proxy,
        verify_ssl=verify_ssl,
    )
    if grant_fallback_response is not None:
        raise _ReturnResponse(grant_fallback_response)

    error_msg, token_response, safe_snapshot = _build_http_error_details(auth, status_exc)
    logger.error(
        "%s for %s on attempt %d/%d",
        error_msg,
        redact_url(auth.token_url),
        attempts_made,
        _MAX_TOKEN_RETRIES,
        extra={"attempt": attempts_made, "response": safe_snapshot},
    )
    auth._audit.log_auth_failure("oauth2", error_msg)
    raise AuthError(
        error_msg,
        details={"token_url": auth.token_url, "token_response": token_response},
    ) from status_exc


def _build_http_error_details(
    auth: Any,
    status_exc: httpx.HTTPStatusError,
) -> tuple[str, dict[str, Any] | None, dict[str, Any]]:
    status_code: int | str = "unknown"
    token_response: dict[str, Any] | None = None
    safe_snapshot: dict[str, Any] = {}
    response = status_exc.response

    if response is not None:
        auth._capture_token_response(response)
        status_code = response.status_code
        token_response = auth._last_token_response
        safe_snapshot = auth._snapshot_response_body(response)

    response_preview = (
        json.dumps(safe_snapshot, ensure_ascii=True, default=str) if safe_snapshot else ""
    )
    if len(response_preview) > 400:
        response_preview = response_preview[:400] + "..."

    error_msg = f"Token endpoint returned HTTP {status_code}"
    if response_preview:
        error_msg += f" (response={response_preview})"
    if token_response is None and response is not None:
        token_response = {
            "status_code": status_code,
            "headers": {},
            "body": safe_snapshot,
            "url": redact_url(auth.token_url) if auth.token_url else "",
            "method": "POST",
        }
    return error_msg, token_response, safe_snapshot


def _handle_transport_failure(
    auth: Any,
    transient_exc: Exception,
    attempt: int,
    attempts_made: int,
    proxy: str | None,
) -> bool:
    if is_connection_refused(transient_exc):
        logger.warning(
            "Token request: connection refused on attempt %d - skipping retries (proxy=%s, url=%s)",
            attempts_made,
            proxy or auth._proxy_label,
            redact_url(auth.token_url),
            extra={"attempt": attempts_made, "error": str(transient_exc)},
        )
        return True

    is_final = attempt == _MAX_TOKEN_RETRIES - 1
    wait_seconds = _RETRY_BACKOFF_BASE**attempt
    if is_final:
        logger.warning(
            "Token request failed on final attempt %d/%d (proxy=%s, url=%s): %s",
            attempts_made,
            _MAX_TOKEN_RETRIES,
            proxy or auth._proxy_label,
            redact_url(auth.token_url),
            transient_exc,
        )
        return False

    logger.debug(
        "Token request failed (attempt %d/%d), retrying in %ds (proxy=%s, url=%s): %s",
        attempts_made,
        _MAX_TOKEN_RETRIES,
        wait_seconds,
        proxy or auth._proxy_label,
        redact_url(auth.token_url),
        transient_exc,
    )
    time.sleep(wait_seconds)
    return False


class _ReturnResponse(Exception):
    def __init__(self, response: httpx.Response):
        super().__init__("return response")
        self.response = response


def apply_post_token_request(
    auth: Any,
    grant_data: dict[str, Any],
    *,
    proxy: str | None = None,
    verify_ssl: bool | None = None,
) -> httpx.Response:
    """Run post_token_request while unwrapping internal control-flow exceptions."""
    try:
        return post_token_request(auth, grant_data, proxy=proxy, verify_ssl=verify_ssl)
    except _ReturnResponse as return_response:
        return return_response.response


def apply_token_response(auth: Any, response: httpx.Response) -> None:
    """Update OAuth2 state from a successful token endpoint response."""
    token_data = _parse_token_json(auth, response)
    raw_access = token_data.get("access_token")
    if not raw_access:
        logger.error(
            "Token endpoint response missing access_token field",
            extra={
                "token_url": redact_url(auth.token_url),
                "status_code": response.status_code,
                "response_keys": list(token_data.keys()),
            },
        )
        raise AuthError("Token endpoint did not return access_token")

    auth.access_token = auth._validate_token_from_endpoint(raw_access, "access_token")
    expires_in_seconds = auth._parse_expires_in(token_data.get("expires_in"))
    auth.expires_at = utc_now() + timedelta(seconds=expires_in_seconds)
    _maybe_update_refresh_token(auth, token_data)
    auth._save_to_storage()

    logger.info("OAuth2 token refreshed successfully")
    auth._audit.log_event(
        AuditEventType.AUTH_TOKEN_REFRESH,
        level=AuditLevel.INFO,
        message=f"OAuth2 token refreshed for client_id={auth.client_id}",
    )


def _parse_token_json(auth: Any, response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except (json.JSONDecodeError, ValueError) as parse_exc:
        error_msg = (
            "Token endpoint returned non-JSON response. "
            f"Status: {response.status_code}, "
            f"Content-Type: {response.headers.get('content-type', 'unknown')}"
        )
        logger.error(
            "%s - Parse error: %s",
            error_msg,
            parse_exc,
            extra={"token_url": redact_url(auth.token_url), "status_code": response.status_code},
        )
        auth._audit.log_auth_failure("oauth2", error_msg)
        raise AuthError(error_msg) from parse_exc
    if not isinstance(payload, dict):
        raise AuthError("Token endpoint returned malformed JSON payload")
    return payload


def _maybe_update_refresh_token(auth: Any, token_data: dict[str, Any]) -> None:
    if "refresh_token" not in token_data:
        return
    try:
        auth.refresh_token = auth._validate_token_from_endpoint(
            token_data["refresh_token"],
            "refresh_token",
        )
    except AuthError:
        logger.warning(
            "Token endpoint returned an invalid refresh_token - keeping previous refresh_token"
        )
