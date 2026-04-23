"""HTTP client façade for the Equinox pipeline.

Orchestrates validation, rate limiting, concurrency, authentication,
retries, and the request/response pipeline through a single :meth:`send` call.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Optional

from equinox.auth.base import AuthStrategy
from equinox.core.audit import get_audit_logger
from equinox.core import error_mapper
from equinox.core import urls
from equinox.core.cookies import CookieManager
from equinox.core.exceptions import RequestError, ValidationError
from equinox.core.interceptors import InterceptorChain, RequestResponseLogger
from equinox.core.log_setup import generate_request_id
from equinox.core.rate_limiter import RateLimiter
from equinox.core.request import Request, Response
from equinox.core.validation import Validator

from equinox.core.client.auth_applier import AuthApplier
from equinox.core.client.concurrency_guard import ConcurrencyGuard
from equinox.core.client.cookie_handler import CookieHandler
from equinox.core.client.dispatcher import HttpxDispatcher
from equinox.core.client.pipeline import RequestPipeline
from equinox.core.client.retry_policy import RetryPolicy

logger = logging.getLogger(__name__)

__all__ = ["HTTPClient"]


class HTTPClient:
    """HTTP client with validation, rate limiting, retries, and interceptors.

    Orchestrates the full request/response lifecycle:

    * Input validation (URL, method, headers, body)
    * Rate limiting (configurable requests/minute)
    * Concurrency control (configurable max simultaneous requests)
    * Authentication via :class:`~equinox.auth.base.AuthStrategy`
    * Timeout and HTTP-overload retries (429 / 503 / 504)
    * SSL/TLS verification with TLS 1.2 minimum
    * Pre/post interceptor chain
    * Audit logging
    """

    # ── Limits and defaults ───────────────────────────────────────────────────

    MAX_TIMEOUT = 300.0           # 5 minutes
    MIN_TIMEOUT = 0.1             # 100 ms
    DEFAULT_TIMEOUT = 30.0
    MAX_REDIRECTS = 10
    MAX_RETRIES = 3               # timeout-retry attempts
    MAX_HTTP_RETRIES = 2          # 429/503/504 retry attempts
    RETRYABLE_STATUS_CODES = {429, 503, 504}
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

        self.timeout = self._clamp_timeout(timeout)
        self.follow_redirects = follow_redirects
        self.verify_ssl = verify_ssl
        self.proxy = proxy
        self.max_rate_per_minute = max_rate_per_minute
        self.max_concurrent_requests = max_concurrent_requests
        self._cancel_event = cancel_event

        self._build_components(cookie_manager)

    # ── Construction helpers ──────────────────────────────────────────────────

    @classmethod
    def _clamp_timeout(cls, timeout: float) -> float:
        """Return *timeout* clamped to ``[MIN_TIMEOUT, MAX_TIMEOUT]``, logging if adjusted."""
        if timeout < cls.MIN_TIMEOUT:
            logger.warning(
                "Timeout %.1fs is below minimum, clamping to %.1fs",
                timeout, cls.MIN_TIMEOUT,
            )
            return cls.MIN_TIMEOUT
        if timeout > cls.MAX_TIMEOUT:
            logger.warning(
                "Timeout %.1fs exceeds maximum, clamping to %.1fs",
                timeout, cls.MAX_TIMEOUT,
            )
            return cls.MAX_TIMEOUT
        return timeout

    def _build_components(self, cookie_manager: Optional[CookieManager]) -> None:
        """Instantiate and wire all collaborator objects.

        Kept separate from ``__init__`` so the constructor stays focused on
        parameter validation while each collaborator has a clear construction
        site here.
        """
        self._active_requests: int = 0  # counter for check_concurrent_limit / _release_concurrent_slot
        self.interceptors = InterceptorChain()
        self.logger = RequestResponseLogger()
        self._audit = get_audit_logger()
        self._rate_limiter = RateLimiter(
            self.max_rate_per_minute,
            window_seconds=self.RATE_LIMIT_WINDOW_SECONDS,
            audit_logger=self._audit,
        )
        self._dispatcher = HttpxDispatcher(
            timeout=self.timeout,
            follow_redirects=self.follow_redirects,
            verify_ssl=self.verify_ssl,
            proxy=self.proxy,
            cookie_handler=CookieHandler(cookie_manager),
        )
        self._concurrency = ConcurrencyGuard(self.max_concurrent_requests)
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

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def active_requests(self) -> int:
        """Number of requests currently in flight."""
        return self._concurrency.active

    # ── Context manager ───────────────────────────────────────────────────────

    def __enter__(self) -> "HTTPClient":
        if self.proxy:
            logger.debug("HTTPClient: opening with proxy %s", self.proxy)
            self.check_proxy_reachable()
        self._dispatcher.open()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self._dispatcher.close()

    # ── Private helpers ───────────────────────────────────────────────────────

    def _interruptible_sleep(self, seconds: float) -> None:
        """Sleep for *seconds*, waking early if the cancel event is set."""
        if self._cancel_event is not None:
            was_cancelled = self._cancel_event.wait(timeout=seconds)
            if was_cancelled:
                raise RequestError("Request cancelled during retry backoff")
        else:
            time.sleep(seconds)

    def _validate_request(self, request: Request) -> None:
        resolved_url = urls.expand_placeholders(
            request.url, getattr(request, "path_params", None)
        )
        Validator.validate_resolved_url(resolved_url)
        Validator.validate_method(request.method)
        if request.headers:
            Validator.validate_headers(request.headers, strict=False)
        if request.params:
            Validator.validate_query_params(request.params)
        if request.body:
            Validator.validate_request_body(request.body, request.headers.get("Content-Type"))

    def _execute_single_attempt(
        self,
        request: Request,
        auth: Optional[AuthStrategy],
    ) -> Response:
        """Perform one HTTP round-trip: apply auth, dispatch, sync cookies."""
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
        self._dispatcher.flush_cookies(response, request.url)
        return response

    def _dispatch_with_retries(
        self,
        request: Request,
        auth: Optional[AuthStrategy],
    ) -> Response:
        response = self._retry_policy.execute_with_http_overload(
            lambda: self._execute_single_attempt(request, auth)
        )
        # Attach retry summary to response for UI display
        retry_summary = self._retry_policy.get_retry_summary()
        if retry_summary:
            response.retry_summary = retry_summary
        return response

    # ── Public API ────────────────────────────────────────────────────────────

    def check_proxy_reachable(self) -> None:
        from equinox.core.proxy import check_proxy_reachable
        check_proxy_reachable(self.proxy)

    def check_concurrent_limit(self) -> None:
        """Acquire one concurrency slot; raises ``RequestError`` if the limit is already reached.

        Increments ``_active_requests`` when a slot is available.  Call
        :meth:`_release_concurrent_slot` to return the slot when the operation
        completes.

        Raises:
            RequestError: If ``_active_requests >= max_concurrent_requests``.
        """
        if self._active_requests >= self.max_concurrent_requests:
            raise RequestError(
                f"Too many concurrent requests: {self._active_requests} active "
                f"(max {self.max_concurrent_requests})"
            )
        self._active_requests += 1

    def _release_concurrent_slot(self) -> None:
        """Return one concurrency slot acquired by :meth:`check_concurrent_limit`.

        Safe to call even when ``_active_requests`` is already 0 — the counter
        is never allowed to go below zero.
        """
        if self._active_requests > 0:
            self._active_requests -= 1

    def check_rate_limit(self) -> int:
        """Trigger the rate limiter and increment the active-request counter.

        Returns:
            The updated ``_active_requests`` count.

        Raises:
            RateLimitError: If the rate limit is exceeded.
        """
        logger.debug("HTTPClient: checking rate limit (max=%d/min)", self.max_rate_per_minute)
        self._rate_limiter.try_acquire()
        self._active_requests += 1
        return self._active_requests

    def send(self, request: Request, auth: Optional[AuthStrategy] = None) -> Response:
        """Send *request*, applying *auth* (or ``request.auth``) if provided.

        Args:
            request: The HTTP request to send.
            auth:    Auth strategy to apply; overrides ``request.auth`` when given.

        Returns:
            The HTTP response.

        Raises:
            ValidationError: If the request fails pre-flight validation.
            RateLimitError:  If the rate limit is exceeded.
            RequestError:    For all other transport or auth failures.
        """
        req_id = generate_request_id()
        logger.info(
            "HTTPClient.send(): method=%s url=%s auth=%s",
            request.method,
            Validator.sanitize_for_display(request.url, 80),
            type(auth).__name__ if auth else "None",
            extra={"request_id": req_id, "method": request.method},
        )

        self._validate_request(request)
        self.check_rate_limit()

        with self._concurrency.slot():
            return self._pipeline.execute(
                request,
                dispatch=lambda req: self._dispatch_with_retries(req, auth),
            )

    # ── Dunder helpers ────────────────────────────────────────────────────────

    def __repr__(self) -> str:
        return (
            f"HTTPClient(timeout={self.timeout}, verify_ssl={self.verify_ssl}, "
            f"proxy={self.proxy!r}, active={self.active_requests}/"
            f"{self.max_concurrent_requests})"
        )
