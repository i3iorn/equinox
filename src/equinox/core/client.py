import json
import os
import ssl
import threading
import time
import logging
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple, Callable

import httpx

from equinox.core.time import utc_now
from equinox.core.request import Request, Response
from equinox.core.exceptions import (
    EquinoxError,
    RequestError,
    RequestTimeoutError,
    RateLimitError,
    CertificateError,
    ValidationError,
)
from equinox.core.validation import Validator
from equinox.core.redact import redact_body, redact_url, redact_headers
from equinox.auth.base import AuthStrategy
from equinox.core.interceptors import InterceptorChain, RequestResponseLogger
from equinox.core.audit import get_audit_logger
from equinox.core.rate_limiter import RateLimiter
from equinox.core import error_mapper
from equinox.core import urls
from equinox.core.cookies import CookieManager
from equinox.core.log_setup import generate_request_id

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Redirect-safe auth wrapper
# ──────────────────────────────────────────────────────────────────────────────


class _RedirectSafeAuth(httpx.Auth):
    """httpx Auth adapter that re-applies auth headers after redirects.

    httpx strips ``Authorization`` (and ``Cookie``) headers when following
    cross-origin redirects (different scheme, host, or port).  This is
    correct per RFC 7235 §2.2 for untrusted redirects, but breaks many
    real-world OAuth2/Bearer flows where the same auth is required
    after a scheme upgrade (HTTP → HTTPS) or a load-balancer redirect.

    By passing auth through httpx's native ``auth`` parameter instead of
    as a plain header, the auth flow is re-executed on every leg of the
    redirect chain, ensuring the ``Authorization`` header is present.
    """

    def __init__(self, auth_headers: Dict[str, str]) -> None:
        self._auth_headers = auth_headers

    def auth_flow(self, request: httpx.Request):
        for key, value in self._auth_headers.items():
            request.headers[key] = value
        yield request


# ──────────────────────────────────────────────────────────────────────────────
# Concurrency guard
# ──────────────────────────────────────────────────────────────────────────────


class ConcurrencyGuard:
    def __init__(self, max_concurrent: int) -> None:
        self._max = max_concurrent
        self._active = 0
        self._lock = threading.Lock()

    def acquire(self) -> None:
        with self._lock:
            if self._active >= self._max:
                raise RequestError(
                    f"Too many concurrent requests: {self._active}/{self._max}"
                )
            self._active += 1
            logger.debug("ConcurrencyGuard acquired: active=%d", self._active)

    def release(self) -> None:
        with self._lock:
            self._active = max(0, self._active - 1)
            logger.debug("ConcurrencyGuard released: active=%d", self._active)


# ──────────────────────────────────────────────────────────────────────────────
# Retry policy (timeouts + HTTP overload)
# ──────────────────────────────────────────────────────────────────────────────


class RetryPolicy:
    def __init__(
        self,
        timeout_retries: int,
        http_retries: int,
        retryable_status_codes: Optional[set] = None,
        retry_after_cap_seconds: float = 60.0,
        interruptible_sleep: Optional[Callable[[float], None]] = None,
    ) -> None:
        self._timeout_retries = max(1, timeout_retries)
        self._http_retries = max(0, http_retries)
        self._retryable_status_codes = retryable_status_codes or {429, 503, 504}
        self._retry_after_cap_seconds = retry_after_cap_seconds
        self._sleep = interruptible_sleep or time.sleep

    def _sleep_backoff(self, attempt: int) -> None:
        wait_seconds = 2 ** attempt  # 1s, 2s, 4s, ...
        logger.warning(
            "Request timed out (attempt %d/%d), retrying in %ds",
            attempt + 1,
            self._timeout_retries,
            wait_seconds,
        )
        self._sleep(wait_seconds)

    def _parse_retry_after(self, response: Response) -> float:
        if not response.headers:
            return 1.0
        try:
            retry_after = float(response.headers.get("retry-after", 1))
        except (ValueError, TypeError):
            retry_after = 1.0
        return min(retry_after, self._retry_after_cap_seconds)

    def execute(self, func: Callable[[], Response]) -> Response:
        # Timeout retries
        for attempt in range(self._timeout_retries):
            try:
                logger.debug(
                    "RetryPolicy: timeout attempt %d/%d",
                    attempt + 1,
                    self._timeout_retries,
                )
                return func()
            except RequestTimeoutError:
                if attempt < self._timeout_retries - 1:
                    self._sleep_backoff(attempt)
                else:
                    logger.error(
                        "Request timed out on final attempt %d/%d, giving up",
                        attempt + 1,
                        self._timeout_retries,
                    )
                    raise
        # This line is only reached when _timeout_retries == 0 (misconfiguration).
        raise RuntimeError("RetryPolicy: _timeout_retries must be >= 1")

    def execute_with_http_overload(self, func: Callable[[], Response]) -> Response:
        """Execute with timeout retries + HTTP overload retries."""
        response = self.execute(func)

        if response.status_code not in self._retryable_status_codes:
            return response

        logger.debug(
            "RetryPolicy: HTTP overload status=%d (retryable=%s)",
            response.status_code,
            response.status_code in self._retryable_status_codes,
        )

        for attempt in range(self._http_retries):
            retry_after = self._parse_retry_after(response)
            logger.warning(
                "Received %d (attempt %d/%d), retrying after %.1fs",
                response.status_code,
                attempt + 1,
                self._http_retries,
                retry_after,
            )
            self._sleep(retry_after)
            response = func()
            logger.debug(
                "HTTP overload retry attempt %d/%d completed, status=%d",
                attempt + 1,
                self._http_retries,
                response.status_code,
            )
            if response.status_code not in self._retryable_status_codes:
                break

        return response


# ──────────────────────────────────────────────────────────────────────────────
# Auth applier
# ──────────────────────────────────────────────────────────────────────────────


class AuthApplier:
    def apply(
        self,
        request: Request,
        headers: Dict[str, str],
        explicit_auth: Optional[AuthStrategy],
        proxy: Optional[str],
    ) -> Dict[str, str]:
        """Apply auth strategy and return the headers that were added.

        Raises:
            RequestError: If authentication fails.
        """
        auth_strategy = explicit_auth or request.auth
        if not auth_strategy:
            return {}

        snapshot = set(headers.keys())
        try:
            if proxy and hasattr(auth_strategy, "_proxy"):
                auth_strategy._proxy = proxy
            logger.debug("Applying auth strategy: %s", type(auth_strategy).__name__)
            auth_strategy.apply(request, headers)
        except Exception as auth_exc:
            safe_msg = redact_body(str(auth_exc), max_length=200) or "unknown error"
            logger.error(
                "Authentication failed (%s): %s — %s",
                type(auth_exc).__name__,
                type(auth_strategy).__name__,
                safe_msg,
            )
            if proxy and (
                "10061" in safe_msg
                or "connection refused" in safe_msg.lower()
                or "econnrefused" in safe_msg.lower()
            ):
                raise RequestError(
                    f"OAuth2 token refresh failed — proxy ({proxy}) is not reachable. "
                    "Please check your proxy settings under Preferences.",
                    details={"proxy": proxy},
                )
            raise RequestError(f"Authentication failed: {safe_msg}")

        auth_headers = {k: headers[k] for k in headers if k not in snapshot}
        if auth_headers:
            logger.debug(
                "Auth applied (%s): %s",
                type(auth_strategy).__name__,
                ", ".join(auth_headers.keys()),
            )
        return auth_headers


# ──────────────────────────────────────────────────────────────────────────────
# Cookie handler
# ──────────────────────────────────────────────────────────────────────────────


class CookieHandler:
    def __init__(self, manager: Optional[CookieManager]) -> None:
        self._manager = manager

    def get_httpx_cookies(self) -> dict:
        if self._manager is not None:
            return self._manager.to_httpx_cookies()
        return {}

    def update_from_response(self, response: Optional[Response], url: str) -> None:
        if self._manager is None or response is None:
            logger.debug("CookieHandler: no manager or response, skipping update")
            return
        try:
            headers = dict(response.headers) if response else {}
            if headers.get("set-cookie"):
                logger.debug("CookieHandler: updating cookie jar from Set-Cookie")
                self._manager.update_from_response(headers, url)
            else:
                logger.debug("CookieHandler: no Set-Cookie header present")
        except Exception as exc:
            logger.debug("CookieHandler: update failed: %s", exc)


# ──────────────────────────────────────────────────────────────────────────────
# Httpx dispatcher
# ──────────────────────────────────────────────────────────────────────────────


class HttpxDispatcher:
    def __init__(
        self,
        timeout: float,
        follow_redirects: bool,
        verify_ssl: bool,
        proxy: Optional[str],
        cookie_handler: CookieHandler,
    ) -> None:
        self._timeout = timeout
        self._follow_redirects = follow_redirects
        self._verify_ssl = verify_ssl
        self._proxy = proxy
        self._cookie_handler = cookie_handler
        self._client: Optional[httpx.Client] = None

    # SSL context builder
    def _build_ssl_context(self) -> Any:
        if not self._verify_ssl:
            return False
        ssl_context = ssl.create_default_context()
        ssl_context.minimum_version = ssl.TLSVersion.TLSv1_2
        return ssl_context

    def _ensure_client(self) -> httpx.Client:
        if self._client is None:
            logger.debug("HttpxDispatcher: creating shared httpx.Client")
            self._client = httpx.Client(
                timeout=self._timeout,
                follow_redirects=self._follow_redirects,
                verify=self._build_ssl_context(),
                proxy=self._proxy,
                cookies=self._cookie_handler.get_httpx_cookies(),
            )
        return self._client

    def _sync_cookies_to_client(self) -> None:
        """Merge the latest CookieManager state into the live httpx.Client jar.

        Called after every response so that cookies received via ``Set-Cookie``
        are available for the next request without rebuilding the whole client.
        """
        if self._client is None:
            return
        try:
            fresh = self._cookie_handler.get_httpx_cookies()
            for name, value in fresh.items():
                self._client.cookies.set(name, value)
        except Exception as exc:
            logger.debug("HttpxDispatcher: failed to sync cookies to client: %s", exc)

    def close(self) -> None:
        if self._client is not None:
            logger.debug("HttpxDispatcher: closing shared httpx.Client")
            self._client.close()
            self._client = None

    # Multipart builder (simple, DRY)
    def _build_multipart_files(
        self, request: Request
    ) -> Tuple[Optional[Dict[str, Any]], List[Any]]:
        """Build httpx-compatible multipart files from request.files (if any).

        Expected shape:
            request.files = {
                "field": ("filename", file_bytes_or_fileobj, "content/type")
            }
        """
        if not getattr(request, "files", None):
            return None, []

        files: Dict[str, Any] = {}
        opened_handles: List[Any] = []

        for field, value in request.files.items():
            # value can be:
            # - (filename, fileobj/bytes, content_type)
            # - Path / str (path)
            if isinstance(value, (str, Path)):
                fh = open(value, "rb")
                opened_handles.append(fh)
                files[field] = (os.path.basename(str(value)), fh)
            elif isinstance(value, tuple) and len(value) in (2, 3):
                files[field] = value
            else:
                raise ValidationError(f"Unsupported file spec for field '{field}'")

        return files, opened_handles

    def _wrap_response(self, raw: httpx.Response, request: Request, elapsed: float) -> Response:
        return Response(
            status_code=raw.status_code,
            reason=self._extract_reason_phrase(raw),
            headers=dict(raw.headers),
            body=raw.content,
            elapsed=elapsed,
            request=request,
            timestamp=utc_now(),
        )

    @staticmethod
    def _extract_reason_phrase(raw: httpx.Response) -> str:
        # httpx doesn't expose reason phrase directly; approximate from status code
        return raw.reason_phrase or httpx.codes.get_reason_phrase(raw.status_code) or ""

    def execute(
        self,
        request: Request,
        headers: Dict[str, str],
        auth_headers: Optional[Dict[str, str]] = None,
    ) -> Response:
        """Execute the request using httpx, handling redirects and multipart."""
        client = self._ensure_client()

        logger.debug(
            "HttpxDispatcher: sending %s request to %s",
            request.method,
            Validator.sanitize_for_display(request.url, 100),
        )
        start_time = time.time()

        httpx_auth: Optional[_RedirectSafeAuth] = None
        if auth_headers:
            for key in auth_headers:
                headers.pop(key, None)
            httpx_auth = _RedirectSafeAuth(auth_headers)

        multipart_files, opened_handles = self._build_multipart_files(request)

        try:
            raw = client.request(
                method=request.method,
                url=request.url,
                headers=headers,
                params=request.params,
                content=(
                    request.body.encode("utf-8")
                    if (request.body and not multipart_files)
                    else None
                ),
                files=multipart_files or None,
                timeout=request.timeout or self._timeout,
                follow_redirects=(
                    request.follow_redirects
                    if request.follow_redirects is not None
                    else self._follow_redirects
                ),
                auth=httpx_auth,
            )
        finally:
            for fh in opened_handles:
                try:
                    fh.close()
                except Exception as close_exc:
                    logger.debug("Failed to close file handle: %s", close_exc)

        if raw.history:
            chain = " → ".join(
                f"{r.status_code} {r.headers.get('location', '?')}" for r in raw.history
            )
            logger.info(
                "Request followed %d redirect(s): %s → %d",
                len(raw.history),
                chain,
                raw.status_code,
            )

        elapsed = time.time() - start_time
        logger.debug(
            "HttpxDispatcher: request completed in %.2fs with status %d",
            elapsed,
            raw.status_code,
        )
        return self._wrap_response(raw, request, elapsed)


# ──────────────────────────────────────────────────────────────────────────────
# Request pipeline (interceptors + audit + error mapping)
# ──────────────────────────────────────────────────────────────────────────────


class RequestPipeline:
    def __init__(
        self,
        interceptors: InterceptorChain,
        audit_logger,
        error_handlers,
    ) -> None:
        self._interceptors = interceptors
        self._audit = audit_logger
        self._error_handlers = error_handlers

    def _handle_error(
        self,
        request: Request,
        error: Exception,
    ) -> None:
        audit_tag: Optional[str] = None
        log_message: Optional[str] = None

        if isinstance(error, EquinoxError):
            # Already a domain error, just log and re-raise
            audit_tag = type(error).__name__
            log_message = str(error)
            # Give interceptors a chance to inspect/transform the domain error.
            # If an interceptor returns a replacement exception, raise it.
            processed = self._interceptors.process_error(request, error)
            if processed is not None:
                raise processed
            # No interceptor suppressed the error — re-raise the original domain error
            raise error
        else:
            # Map via error_handlers
            for exc_type, handler_fn in self._error_handlers:
                if isinstance(error, exc_type):
                    logger.debug("Error matched handler for %s", exc_type.__name__)
                    kwargs = handler_fn(error, request)
                    mapped_error = kwargs.get("error")
                    audit_tag = kwargs.get("audit_tag")
                    log_message = kwargs.get("log_message")
                    if audit_tag:
                        self._audit.log_request(
                            request.method, request.url, error=audit_tag
                        )
                    if log_message:
                        logger.warning("Error log message: %s", log_message)
                    processed = self._interceptors.process_error(request, mapped_error)
                    if processed is not None:
                        raise processed
                    return  # suppressed
            # Fallback
            safe_msg = redact_body(str(error), max_length=500) or ""
            fallback = RequestError(
                f"Request failed: {type(error).__name__}: {safe_msg}",
                details={"error": type(error).__name__},
            )
            audit_tag = type(error).__name__
            log_message = (
                f"Unexpected error during request: {type(error).__name__}: {safe_msg}"
            )
            self._audit.log_request(request.method, request.url, error=audit_tag)
            logger.warning("Error log message: %s", log_message)
            processed = self._interceptors.process_error(request, fallback)
            if processed is not None:
                raise processed

    def execute(
        self,
        request: Request,
        dispatch: Callable[[Request], Response],
    ) -> Response:
        logger.debug("RequestPipeline: starting")
        try:
            logger.debug("RequestPipeline: running pre-request interceptors")
            request = self._interceptors.process_request(request)

            response = dispatch(request)

            logger.debug("RequestPipeline: running post-response interceptors")
            response = self._interceptors.process_response(request, response)

            logger.debug("RequestPipeline: logging successful request to audit trail")
            self._audit.log_request(
                request.method, redact_url(request.url), status_code=response.status_code
            )

            logger.debug(
                "RequestPipeline: completed method=%s status=%d elapsed=%.2fs",
                request.method,
                response.status_code,
                response.elapsed,
            )
            return response

        except Exception as exc:
            logger.debug(
                "RequestPipeline: caught exception type=%s message=%s",
                type(exc).__name__,
                str(exc)[:200],
            )
            self._handle_error(request, exc)
            # If error was suppressed by interceptors, raise a generic error
            raise RequestError("Request was suppressed by an interceptor")


# ──────────────────────────────────────────────────────────────────────────────
# HTTPClient façade
# ──────────────────────────────────────────────────────────────────────────────


class HTTPClient:
    """HTTP Client for making requests with security features.

    Features:
    - Input validation
    - Rate limiting
    - Timeout & HTTP overload retries
    - SSL/TLS verification
    - Concurrency control
    - Comprehensive error handling
    """

    MAX_TIMEOUT = 300.0  # 5 minutes
    MIN_TIMEOUT = 0.1    # 100ms
    DEFAULT_TIMEOUT = 30.0
    MAX_REDIRECTS = 10
    MAX_RETRIES = 3
    RETRYABLE_STATUS_CODES = {429, 503, 504}
    MAX_HTTP_RETRIES = 2
    RETRY_AFTER_CAP_SECONDS = 60.0
    RATE_LIMIT_WINDOW_SECONDS = 60

    def __init__(
        self,
        timeout: float = 30.0,
        follow_redirects: bool = True,
        verify_ssl: bool = True,
        proxy: Optional[str] = None,
        max_rate_per_minute: int = 60,
        max_concurrent_requests: int = 10,
        cookie_manager: Optional[CookieManager] = None,
        cancel_event: Optional[threading.Event] = None,
    ):
        if not isinstance(timeout, (int, float)) or timeout <= 0:
            raise ValidationError("Timeout must be a positive number")

        if timeout < self.MIN_TIMEOUT:
            logger.warning(
                "Timeout %ss is very low, using minimum %ss",
                timeout,
                self.MIN_TIMEOUT,
            )
            timeout = self.MIN_TIMEOUT
        elif timeout > self.MAX_TIMEOUT:
            logger.warning(
                "Timeout %ss exceeds maximum, using %ss",
                timeout,
                self.MAX_TIMEOUT,
            )
            timeout = self.MAX_TIMEOUT

        self.timeout = timeout
        self.follow_redirects = follow_redirects
        self.verify_ssl = verify_ssl
        self.proxy = proxy
        self.max_rate_per_minute = max_rate_per_minute
        # Public attribute expected by callers/tests
        self.max_concurrent_requests = max_concurrent_requests
        self._cancel_event = cancel_event

        self.interceptors = InterceptorChain()
        self.logger = RequestResponseLogger()
        self._audit = get_audit_logger()
        self._rate_limiter = RateLimiter(
            self.max_rate_per_minute,
            window_seconds=self.RATE_LIMIT_WINDOW_SECONDS,
            audit_logger=self._audit,
        )
        self._cookie_handler = CookieHandler(cookie_manager)
        self._dispatcher = HttpxDispatcher(
            timeout=self.timeout,
            follow_redirects=self.follow_redirects,
            verify_ssl=self.verify_ssl,
            proxy=self.proxy,
            cookie_handler=self._cookie_handler,
        )
        self._concurrency = ConcurrencyGuard(max_concurrent_requests)
        self._auth_applier = AuthApplier()
        self._retry_policy = RetryPolicy(
            timeout_retries=self.MAX_RETRIES,
            http_retries=self.MAX_HTTP_RETRIES,
            retryable_status_codes=self.RETRYABLE_STATUS_CODES,
            retry_after_cap_seconds=self.RETRY_AFTER_CAP_SECONDS,
            interruptible_sleep=self._interruptible_sleep,
        )
        self._error_handlers = error_mapper.build_error_handlers(self)
        self._pipeline = RequestPipeline(
            interceptors=self.interceptors,
            audit_logger=self._audit,
            error_handlers=self._error_handlers,
        )
        # Track active requests for testing and instrumentation
        self._active_requests = 0

    # Context manager delegates to dispatcher
    def __enter__(self):
        if self.proxy:
            logger.debug("HTTPClient: opening with proxy %s", self.proxy)
            self._check_proxy_reachable()
        # Ensure dispatcher client is created
        self._dispatcher._ensure_client()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._dispatcher.close()

    # Proxy reachability
    def _check_proxy_reachable(self) -> None:
        from equinox.core.proxy import check_proxy_reachable

        if not self.proxy:
            logger.debug("Proxy check skipped: no proxy configured")
            return

        check_proxy_reachable(self.proxy)

    # Interruptible sleep
    def _interruptible_sleep(self, seconds: float) -> None:
        if self._cancel_event is not None:
            cancelled = self._cancel_event.wait(timeout=seconds)
            if cancelled:
                raise RequestError("Request cancelled during retry backoff")
        else:
            time.sleep(seconds)

    # Validation
    def _validate_request(self, request: Request) -> None:
        resolved_url = urls.expand_placeholders(
            request.url, getattr(request, "path_params", None) or None
        )
        Validator.validate_resolved_url(resolved_url)
        Validator.validate_method(request.method)

        if request.headers:
            Validator.validate_headers(request.headers, strict=False)

        if request.params:
            Validator.validate_query_params(request.params)

        if request.body:
            content_type = request.headers.get("Content-Type")
            Validator.validate_request_body(request.body, content_type)

    def check_rate_limit(self) -> None:
        """"
        Wrapper used by tests and callers to trigger the rate limiter logic directly.
        """
        logger.debug("HTTPClient: checking rate limit (max=%d/min)", self.max_rate_per_minute)
        self._rate_limiter.try_acquire()

    # Concurrency helpers used by tests
    def _check_concurrent_limit(self) -> None:
        """Attempt to acquire a concurrent request slot. Raises RequestError
        if the configured concurrency limit is exceeded."""
        self._concurrency.acquire()
        self._active_requests = getattr(self, "_active_requests", 0) + 1

    def _release_concurrent_slot(self) -> None:
        """Release a previously acquired concurrent slot. Never lets the
        internal active counter drop below zero."""
        try:
            self._concurrency.release()
        except Exception as exc:
            logger.debug("Failed to release concurrency semaphore: %s", exc)
        self._active_requests = max(0, getattr(self, "_active_requests", 0) - 1)

    # Public API
    def send(self, request: Request, auth: Optional[AuthStrategy] = None) -> Response:
        req_id = generate_request_id()
        logger.info(
            "HTTPClient.send(): method=%s url=%s auth=%s",
            request.method,
            Validator.sanitize_for_display(request.url, 80),
            type(auth).__name__ if auth else "None",
            extra={"request_id": req_id, "method": request.method},
        )

        # Validation
        self._validate_request(request)

        # Rate limiting
        logger.debug(
            "HTTPClient: checking rate limit (max=%d/min)", self.max_rate_per_minute,
            extra={"request_id": req_id},
        )
        self._rate_limiter.try_acquire()

        # Concurrency
        self._concurrency.acquire()
        try:
            return self._pipeline.execute(
                request,
                dispatch=lambda req: self._dispatch_with_retries(req, auth),
            )
        finally:
            self._concurrency.release()

    # Internal dispatch with retries
    def _dispatch_with_retries(
        self,
        request: Request,
        auth: Optional[AuthStrategy],
    ) -> Response:
        def _single_call() -> Response:
            headers = dict(request.headers) if request.headers else {}
            auth_headers = self._auth_applier.apply(
                request=request,
                headers=headers,
                explicit_auth=auth,
                proxy=self.proxy,
            )
            response = self._dispatcher.execute(
                request=request,
                headers=headers,
                auth_headers=auth_headers,
            )
            self._cookie_handler.update_from_response(response, request.url)
            # Merge newly-received cookies back into the live httpx.Client jar
            # so the next request (or retry) sends the updated cookies.
            self._dispatcher._sync_cookies_to_client()
            return response

        return self._retry_policy.execute_with_http_overload(_single_call)