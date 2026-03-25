"""HTTP Client implementation using httpx"""
import json
import os
import ssl
import threading

import httpx
import time
import logging
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple
from threading import Lock

from equinox.core.time import utc_now
from equinox.core.request import Request, Response
from equinox.core.exceptions import (
    EquinoxError, RequestError, RequestTimeoutError, RateLimitError,
    CertificateError, ValidationError
)
from equinox.core.validation import Validator
from equinox.core.redact import redact_body, redact_url
from equinox.auth.base import AuthStrategy
from equinox.core.interceptors import InterceptorChain, RequestResponseLogger
from equinox.core.audit import get_audit_logger
from equinox.core.rate_limiter import RateLimiter
from equinox.core import urls

logger = logging.getLogger(__name__)


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


def _is_ssl_error(exc: Exception) -> bool:
    """Return True if *exc* (typically ``httpx.ConnectError``) wraps an SSL failure.

    In httpx ≥0.24 there is no dedicated ``SSLError``; SSL failures surface
    as ``ConnectError`` whose cause chain contains an ``ssl.SSLError``.

    Follows both ``__cause__`` and ``__context__`` (``raise exc from None``
    clears ``__cause__`` but preserves ``__context__``).
    """
    seen: set = set()
    stack = [exc]
    while stack:
        e = stack.pop()
        eid = id(e)
        if eid in seen:
            continue
        seen.add(eid)
        if isinstance(e, ssl.SSLError):
            return True
        cause = getattr(e, "__cause__", None)
        if cause is not None:
            stack.append(cause)
        context = getattr(e, "__context__", None)
        if context is not None:
            stack.append(context)
    # Fallback: check the string representation
    msg = str(exc).lower()
    return "ssl" in msg or "certificate" in msg


def _is_proxy_error(exc: Exception) -> bool:
    """Return True if *exc* originated from a proxy connection attempt.

    httpcore raises the same ``ConnectError`` for direct and proxied
    connections.  We detect proxy involvement by checking whether any
    traceback frame in the full exception graph originates from httpcore's
    ``http_proxy`` module.

    The walk follows both ``__cause__`` *and* ``__context__`` so that
    ``raise exc from None`` (used in httpcore's connection pool) does not
    silently break detection — Python clears ``__cause__`` but preserves
    ``__context__`` in that case.  A *seen* set prevents infinite loops on
    circular exception chains.
    """
    seen: set = set()
    stack = [exc]
    while stack:
        e = stack.pop()
        eid = id(e)
        if eid in seen:
            continue
        seen.add(eid)
        tb = e.__traceback__
        while tb is not None:
            if "http_proxy" in (tb.tb_frame.f_code.co_filename or ""):
                return True
            tb = tb.tb_next
        cause = getattr(e, "__cause__", None)
        if cause is not None:
            stack.append(cause)
        context = getattr(e, "__context__", None)
        if context is not None:
            stack.append(context)
    return False


class HTTPClient:
    """HTTP Client for making requests with security features.

    Features:
    - Input validation
    - Rate limiting
    - Timeout controls
    - SSL/TLS verification
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
        cookie_manager: Optional[Any] = None,
        cancel_event: Optional[threading.Event] = None,
    ):
        """Initialize HTTP client.

        Args:
            timeout: Request timeout in seconds (0.1 to 300)
            follow_redirects: Whether to follow redirects
            verify_ssl: Whether to verify SSL certificates
            proxy: Proxy URL (e.g., 'http://localhost:8080')
            max_rate_per_minute: Maximum requests per minute (0 = unlimited)
            max_concurrent_requests: Maximum concurrent requests
            cookie_manager:
            cancel_event:

        Raises:
            ValidationError: If parameters are invalid
        """
        if not isinstance(timeout, (int, float)) or timeout <= 0:
            raise ValidationError("Timeout must be a positive number")

        if timeout < self.MIN_TIMEOUT:
            logger.warning(f"Timeout {timeout}s is very low, using minimum {self.MIN_TIMEOUT}s")
            timeout = self.MIN_TIMEOUT
        elif timeout > self.MAX_TIMEOUT:
            logger.warning(f"Timeout {timeout}s exceeds maximum, using {self.MAX_TIMEOUT}s")
            timeout = self.MAX_TIMEOUT

        self.timeout = timeout
        self.follow_redirects = follow_redirects
        self.verify_ssl = verify_ssl
        self.proxy = proxy
        self.max_rate_per_minute = max_rate_per_minute
        self.max_concurrent_requests = max_concurrent_requests
        self._cookie_manager = cookie_manager
        self._cancel_event = cancel_event

        self._client: Optional[httpx.Client] = None

        # legacy fields removed — rate limiting is handled by RateLimiter

        self._active_requests = 0
        self._request_lock = Lock()

        self.interceptors = InterceptorChain()
        self.logger = RequestResponseLogger()
        self._audit = get_audit_logger()
        # Encapsulated rate limiter (uses audit logger for violation events)
        self._rate_limiter = RateLimiter(self.max_rate_per_minute, window_seconds=self.RATE_LIMIT_WINDOW_SECONDS, audit_logger=self._audit)
        self._error_handlers = self._build_error_handlers()

    def __enter__(self):
        if self.proxy:
            logger.debug("Opening HTTPClient with proxy: %s", self.proxy)
            self._check_proxy_reachable()
        self._client = httpx.Client(
            timeout=self.timeout,
            follow_redirects=self.follow_redirects,
            verify=self._build_ssl_context(),
            proxy=self.proxy,
            cookies=self._get_current_cookies(),
        )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._client:
            self._client.close()
            self._client = None

    def _check_proxy_reachable(self) -> None:
        """Verify the configured proxy is accepting TCP connections.

        Uses a non-blocking ``connect()`` + ``select()`` checking **both** the
        writable and exceptional file-descriptor sets.

        Cross-platform behaviour of non-blocking connect() on a refused port:

        * **Unix** — socket appears in the *writable* set; ``SO_ERROR`` is
          ``ECONNREFUSED``.
        * **Windows** — socket appears in the *exceptional* set; ``SO_ERROR``
          is ``WSAECONNREFUSED`` (10061).  Our previous implementation only
          checked the writable set, so Windows refused connections always
          looked like timeouts.

        A blocking ``settimeout()`` socket was tried as an alternative but
        Windows also delays the RST on loopback for ~3 s, so it timed out too.

        Raises:
            RequestError: If the proxy actively refuses the connection.
        """
        import errno
        import select as _select
        import socket
        from urllib.parse import urlparse

        parsed = urlparse(self.proxy)
        host = parsed.hostname
        port = parsed.port or 8080
        if not host:
            logger.debug("Proxy check skipped: no hostname in proxy URL")
            return

        logger.debug(
            "Proxy details: scheme=%s hostname=%s port=%d netloc=%s",
            parsed.scheme, host, port, parsed.netloc,
        )

        # On Windows, loopback RSTs may take ~3 s — use a generous timeout so
        # the select() actually has a chance to observe the refusal.
        is_loopback = host in ("127.0.0.1", "::1", "localhost")
        connect_timeout = 3.5 if is_loopback else 1.5
        _REFUSED = {errno.ECONNREFUSED, getattr(errno, "WSAECONNREFUSED", 10061)}

        logger.debug(
            "Pre-flight proxy reachability check: %s:%s (timeout=%.1fs, loopback=%s)",
            host, port, connect_timeout, is_loopback,
        )

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setblocking(False)
        try:
            logger.debug("Attempting non-blocking connect to %s:%s", host, port)
            sock.connect((host, port))
            # Immediate success — very unusual on non-blocking, but handle it.
            logger.debug("Proxy pre-flight: %s:%s connected immediately", host, port)

        except BlockingIOError as bio_err:
            logger.debug(
                "Proxy pre-flight: BlockingIOError on connect (expected): %s", bio_err,
            )
            # EINPROGRESS / WSAEWOULDBLOCK — wait for the OS verdict.
            # Check BOTH writable (Unix success/fail) AND exceptional (Windows fail).
            _, writable, exceptional = _select.select(
                [], [sock], [sock], connect_timeout
            )
            logger.debug(
                "Proxy pre-flight select() after %.1fs: writable=%s exceptional=%s",
                connect_timeout, bool(writable), bool(exceptional),
            )

            if exceptional or writable:
                err = sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
                logger.debug(
                    "Proxy pre-flight SO_ERROR for %s:%s = %d", host, port, err,
                )
                if err in _REFUSED:
                    logger.warning(
                        "Proxy pre-flight failed — %s:%s refused connection (errno %d). "
                        "Proxy is not running or not accepting connections on this port.",
                        host, port, err,
                    )
                    raise RequestError(
                        f"Failed to connect to proxy ({self.proxy}). "
                        "The proxy server is not running or refusing connections. "
                        "Please check your proxy settings under Preferences and ensure "
                        "the proxy server is running and configured correctly.",
                        details={
                            "proxy": self.proxy,
                            "host": host,
                            "port": port,
                            "errno": err,
                            "error_type": "connection_refused",
                        },
                    )
                if err != 0:
                    logger.debug(
                        "Proxy pre-flight: SO_ERROR %d for %s:%s (errno name: %s) — deferring to httpx",
                        err, host, port, errno.errorcode.get(err, "unknown"),
                    )
                else:
                    logger.debug("Proxy pre-flight: %s:%s is reachable", host, port)
            else:
                logger.debug(
                    "Proxy pre-flight: select() timed out for %s:%s (%.1fs) — deferring to httpx",
                    host, port, connect_timeout,
                )

        except OSError as os_err:
            errno_name = errno.errorcode.get(os_err.errno, "unknown")
            logger.debug(
                "Proxy pre-flight OSError for %s:%s (errno %d = %s): %s",
                host, port, os_err.errno, errno_name, os_err,
            )
            if os_err.errno in _REFUSED:
                logger.warning(
                    "Proxy pre-flight failed — %s:%s refused connection (errno %d = %s)",
                    host, port, os_err.errno, errno_name,
                )
                raise RequestError(
                    f"Failed to connect to proxy ({self.proxy}). "
                    "The proxy server is not running or refusing connections. "
                    "Please check your proxy settings under Preferences and ensure "
                    "the proxy server is running and configured correctly.",
                    details={
                        "proxy": self.proxy,
                        "host": host,
                        "port": port,
                        "errno": os_err.errno,
                        "errno_name": errno_name,
                        "error_type": "connection_refused",
                    },
                )
            logger.debug(
                "Proxy pre-flight socket error for %s:%s (errno %d = %s, will defer to httpx): %s",
                host, port, os_err.errno, errno_name, os_err,
            )

        finally:
            sock.close()
            logger.debug("Proxy pre-flight: socket closed for %s:%s", host, port)

    def _build_ssl_context(self) -> Any:
        """Build an SSL context enforcing TLS 1.2+ minimum, or False to skip verification."""
        if not self.verify_ssl:
            return False
        ssl_context = ssl.create_default_context()
        ssl_context.minimum_version = ssl.TLSVersion.TLSv1_2
        return ssl_context

    def _get_current_cookies(self) -> dict:
        """Return the current cookie jar as an httpx-compatible dict."""
        if self._cookie_manager is not None:
            return self._cookie_manager.to_httpx_cookies()
        return {}

    def _check_rate_limit(self) -> None:
        """Delegate rate-limit enforcement to the encapsulated RateLimiter."""
        self._rate_limiter.try_acquire()

    def _check_concurrent_limit(self) -> None:
        """Raise RequestError if the concurrent request cap is reached."""
        with self._request_lock:
            if self._active_requests >= self.max_concurrent_requests:
                raise RequestError(
                    f"Too many concurrent requests: "
                    f"{self._active_requests}/{self.max_concurrent_requests}"
                )
            self._active_requests += 1

    def _release_concurrent_slot(self) -> None:
        """Decrement the active-request counter, never below zero."""
        with self._request_lock:
            self._active_requests = max(0, self._active_requests - 1)

    def send(self, request: Request, auth: Optional[AuthStrategy] = None) -> Response:
        """Send HTTP request with validation and security checks.

        Args:
            request: Request object
            auth: Optional auth strategy

        Returns:
            Response object

        Raises:
            ValidationError: If request validation fails
            RateLimitError: If rate limit exceeded
            RequestError: If request fails
            TimeoutError: If request times out
            CertificateError: If SSL verification fails
        """
        logger.debug(
            "send() called: method=%s url=%s auth=%s",
            request.method,
            Validator.sanitize_for_display(request.url, 80),
            type(auth).__name__ if auth else "None",
        )

        try:
            self._validate_request(request)
            logger.debug("Request validation passed for %s", request.method)
        except ValidationError as validation_error:
            logger.error("Request validation failed: %s", type(validation_error).__name__)
            raise

        try:
            logger.debug("Checking rate limit (max=%d/min)", self.max_rate_per_minute)
            self._check_rate_limit()
            logger.debug("Rate limit check passed")
        except RateLimitError as rate_error:
            logger.warning("Rate limit exceeded: %s", rate_error)
            raise

        try:
            logger.debug(
                "Checking concurrent request limit (active=%d, max=%d)",
                self._active_requests, self.max_concurrent_requests,
            )
            self._check_concurrent_limit()
            logger.debug("Concurrent request limit check passed")
        except RequestError as concurrent_error:
            logger.warning("Concurrent request limit exceeded: %s", concurrent_error)
            raise

        try:
            response = self._send_with_timeout_retries(request, auth)
            response = self._retry_on_server_overload(request, auth, response)
            if response is None:
                raise RequestError(
                    "Request was suppressed by an interceptor",
                    details={"url": request.url},
                )
            logger.debug("send() completed successfully: status=%d", response.status_code)
            return response
        finally:
            self._release_concurrent_slot()
            logger.debug("Concurrent request slot released (active=%d)", self._active_requests)

    def _send_with_timeout_retries(
        self,
        request: Request,
        auth: Optional[AuthStrategy],
    ) -> Optional[Response]:
        """Send the request, retrying on transient TimeoutErrors with exponential backoff.

        Non-retriable errors (SSL, rate-limit, validation) propagate immediately.
        """
        last_error: Optional[Exception] = None
        response: Optional[Response] = None

        for attempt in range(self.MAX_RETRIES):
            try:
                logger.debug(
                    "_send_with_timeout_retries: attempt %d/%d",
                    attempt + 1, self.MAX_RETRIES,
                )
                response = self._send_once(request, auth)
                logger.debug("Request succeeded on attempt %d/%d", attempt + 1, self.MAX_RETRIES)
                return response
            except RequestTimeoutError as timeout_error:
                last_error = timeout_error
                if attempt < self.MAX_RETRIES - 1:
                    wait_seconds = 2 ** attempt  # 1s, 2s, 4s
                    logger.warning(
                        "Request timed out (attempt %d/%d), retrying in %ds",
                        attempt + 1, self.MAX_RETRIES, wait_seconds,
                    )
                    self._interruptible_sleep(wait_seconds)
                else:
                    logger.error(
                        "Request timed out on final attempt %d/%d, giving up",
                        attempt + 1, self.MAX_RETRIES,
                    )
                    raise

        if last_error is not None and response is None:
            raise last_error  # unreachable — satisfies type checkers

        return response

    def _interruptible_sleep(self, seconds: float) -> None:
        """Sleep for *seconds*, but wake immediately if the cancel event fires.

        Uses ``threading.Event.wait`` instead of ``time.sleep`` so that
        ``RequestWorker.cancel()`` can interrupt a retry backoff instantly
        rather than waiting for the full sleep to expire.

        Raises:
            RequestError: If the cancel event was set during the sleep.
        """
        if self._cancel_event is not None:
            cancelled = self._cancel_event.wait(timeout=seconds)
            if cancelled:
                raise RequestError("Request cancelled during retry backoff")
        else:
            time.sleep(seconds)

    def _retry_on_server_overload(
        self,
        request: Request,
        auth: Optional[AuthStrategy],
        response: Optional[Response],
    ) -> Optional[Response]:
        """Retry the request when the server signals overload (429/503/504).

        Respects the Retry-After header, capped at 60 seconds.
        """
        if response is None or response.status_code not in self.RETRYABLE_STATUS_CODES:
            return response

        logger.debug(
            "_retry_on_server_overload: status=%d (retryable=%s)",
            response.status_code,
            response.status_code in self.RETRYABLE_STATUS_CODES,
        )

        for attempt in range(self.MAX_HTTP_RETRIES):
            retry_after = self._parse_retry_after(response)
            logger.warning(
                "Received %d (attempt %d/%d), retrying after %.1fs",
                response.status_code, attempt + 1, self.MAX_HTTP_RETRIES, retry_after,
            )
            self._interruptible_sleep(retry_after)
            response = self._send_once(request, auth)
            logger.debug(
                "Retry attempt %d/%d completed, status=%d",
                attempt + 1, self.MAX_HTTP_RETRIES, response.status_code if response else 0,
            )
            if response is None or response.status_code not in self.RETRYABLE_STATUS_CODES:
                break

        return response

    def _parse_retry_after(self, response: Response) -> float:
        """Extract the Retry-After wait duration from a response, capped at 60s."""
        if not response.headers:
            return 1.0
        try:
            retry_after = float(response.headers.get("retry-after", 1))
        except (ValueError, TypeError):
            retry_after = 1.0
        return min(retry_after, self.RETRY_AFTER_CAP_SECONDS)

    def _send_once(
        self,
        request: Request,
        auth: Optional[AuthStrategy],
    ) -> Optional[Response]:
        """Send the request exactly once, using a managed or standalone client."""
        if self._client is None:
            with self:
                return self._send_internal(request, auth)
        return self._send_internal(request, auth)

    def _validate_request(self, request: Request) -> None:
        """Validate all components of the request before sending."""
        # Expand placeholders from request.path_params (if any) before resolved validation.
        resolved_url = urls.expand_placeholders(request.url, getattr(request, "path_params", None) or None)
        Validator.validate_resolved_url(resolved_url)
        Validator.validate_method(request.method)

        if request.headers:
            Validator.validate_headers(request.headers, strict=False)

        if request.params:
            Validator.validate_query_params(request.params)

        if request.body:
            content_type = request.headers.get('Content-Type')
            Validator.validate_request_body(request.body, content_type)

    # ── Exception-to-error mapping for _send_internal ───────────────────
    # Each entry: (exception_type, handler_fn).
    # Ordered most-specific-first (Python matches the first handler).
    # Built once in __init__ via _build_error_handlers(); lambdas capture
    # ``self`` so instance attributes (proxy, timeout) are read at call time.

    def _build_error_handlers(self):
        """Build and return the (exc_type, handler_fn) list."""
        return [
            (
                httpx.ConnectTimeout,
                lambda exc, req: dict(
                    error=RequestTimeoutError("Connection timed out", details={"url": redact_url(req.url)}),
                    log_message=f"Connection timeout for {redact_url(req.url)}",
                ),
            ),
            (
                httpx.ReadTimeout,
                lambda exc, req: dict(
                    error=RequestTimeoutError("Server response timed out", details={"url": redact_url(req.url)}),
                    log_message=f"Read timeout for {redact_url(req.url)}",
                ),
            ),
            (
                httpx.TimeoutException,
                lambda exc, req: dict(
                    error=RequestTimeoutError(
                        f"Request timed out after {self.timeout} seconds",
                        details={"url": redact_url(req.url), "timeout": self.timeout},
                    ),
                    audit_tag="timeout",
                    log_message=f"Request timeout after {self.timeout}s for {redact_url(req.url)}",
                ),
            ),
            (
                httpx.ConnectError,
                lambda exc, req: (
                    dict(
                        error=CertificateError(
                            "SSL certificate verification failed. "
                            "The server's certificate is invalid or untrusted.",
                            details={"url": redact_url(req.url)},
                        ),
                        log_message=(
                            f"SSL certificate verification failed for {redact_url(req.url)}"
                            + (f": {exc}" if str(exc) else "")
                        ),
                    )
                    if _is_ssl_error(exc)
                    else (
                        dict(
                            error=RequestError(
                                f"Failed to connect to proxy ({self.proxy}). "
                                "Please check your proxy settings under Preferences.",
                                details={"url": redact_url(req.url), "proxy": self.proxy},
                            ),
                            log_message=(
                                f"Proxy connection error ({self.proxy})"
                                + (f": {exc}" if str(exc) else "")
                                + f" — for {redact_url(req.url)}"
                            ),
                        )
                        if self.proxy and _is_proxy_error(exc)
                        else dict(
                            error=RequestError(
                                "Failed to connect to server. "
                                "Please check the URL and your network connection.",
                                details={"url": redact_url(req.url)},
                            ),
                            log_message=(
                                f"Connection error for {redact_url(req.url)}"
                                + (f": {exc}" if str(exc) else "")
                            ),
                        )
                    )
                ),
            ),
            (
                httpx.TooManyRedirects,
                lambda exc, req: dict(
                    error=RequestError(
                        f"Too many redirects (max: {self.MAX_REDIRECTS})",
                        details={"url": redact_url(req.url)},
                    ),
                    log_message=f"Too many redirects for {redact_url(req.url)}",
                ),
            ),
            (
                httpx.HTTPStatusError,
                lambda exc, req: dict(
                    error=RequestError(
                        f"HTTP error: {exc.response.status_code}",
                        details={"url": redact_url(req.url), "status": exc.response.status_code},
                    ),
                    log_message=f"HTTP error status {exc.response.status_code} for {redact_url(req.url)}",
                ),
            ),
            (
                httpx.HTTPError,
                lambda exc, req: dict(
                    error=RequestError(
                        "HTTP request failed",
                        details={"url": redact_url(req.url)},
                    ),
                    log_message=f"HTTP error for {redact_url(req.url)}",
                ),
            ),
            (
                UnicodeEncodeError,
                lambda exc, req: dict(
                    error=RequestError(
                        "Request body contains invalid characters",
                        details={},
                    ),
                    log_message="Encoding error in request body",
                ),
            ),
        ]

    def _send_internal(self, request: Request, auth: Optional[AuthStrategy] = None) -> Response:
        """Send the request through the interceptor chain, applying auth and error handling.

        Uses a data-driven error map so each exception type is handled
        consistently without repetitive ``except`` blocks.
        """
        logger.debug("_send_internal() starting")
        try:
            logger.debug("Running pre-request interceptors")
            request = self.interceptors.process_request(request)
            headers = dict(request.headers) if request.headers else {}
            
            logger.debug("Applying authentication (strategy=%s)", type(auth or request.auth).__name__)
            auth_headers = self._apply_auth(request, headers, auth)
            if auth_headers:
                logger.debug("Auth headers applied: %s", list(auth_headers.keys()))

            logger.debug("Dispatching request via httpx")
            response = self._dispatch_request(request, headers, auth_headers)
            
            logger.debug("Updating cookie jar from response")
            self._update_cookie_jar(request, response)
            
            logger.debug("Running post-response interceptors")
            response = self.interceptors.process_response(request, response)
            
            logger.debug("Logging successful request to audit trail")
            self._audit.log_request(
                request.method, redact_url(request.url), status_code=response.status_code
            )
            logger.debug(
                "_send_internal() completed: method=%s status=%d elapsed=%.2fs",
                request.method, response.status_code, response.elapsed,
            )
            return response

        except Exception as exc:
            logger.debug(
                "_send_internal() caught exception: type=%s message=%s",
                type(exc).__name__, str(exc)[:100],
            )
            # Let our own exceptions (auth errors, validation, etc.)
            # propagate with their original message intact.
            if isinstance(exc, EquinoxError):
                logger.debug("Exception is EquinoxError subclass, re-raising: %s", exc)
                raise

            # Walk the handler list; first matching type wins
            for exc_type, handler_fn in self._error_handlers:
                if isinstance(exc, exc_type):
                    logger.debug(
                        "Exception matched handler for %s", exc_type.__name__,
                    )
                    kwargs = handler_fn(exc, request)
                    return self._handle_error(request, **kwargs)

            # Generic fallback for truly unexpected errors
            logger.warning(
                "No handler matched for exception type %s, using fallback",
                type(exc).__name__,
            )
            safe_msg = redact_body(str(exc), max_length=500) or ""
            return self._handle_error(
                request,
                error=RequestError(
                    f"Request failed: {type(exc).__name__}: {safe_msg}",
                    details={"error": type(exc).__name__},
                ),
                audit_tag=f"{type(exc).__name__}",
                log_message=(
                    f"Unexpected error during request: "
                    f"{type(exc).__name__}: {safe_msg}"
                ),
            )

    def _apply_auth(
        self,
        request: Request,
        headers: Dict[str, str],
        explicit_auth: Optional[AuthStrategy],
    ) -> Dict[str, str]:
        """Apply the auth strategy and return the auth-injected headers separately.

        Auth headers (e.g. ``Authorization``) are added to *headers* for
        backward compatibility, but also returned in a separate dict so the
        caller can pass them through httpx's native ``auth`` mechanism to
        survive cross-origin redirects.

        Returns:
            Dict of headers that were added by the auth strategy (empty if
            no auth is configured).

        Raises:
            RequestError: If authentication fails.
        """
        auth_strategy = explicit_auth or request.auth
        if not auth_strategy:
            return {}

        snapshot = set(headers.keys())
        try:
            # Forward the active proxy so OAuth2 token fetches (and any future
            # auth strategy that honours _proxy) route through the same proxy
            # as the main request.  Attribute injection is safe — strategies
            # that don't use _proxy simply ignore it.
            if self.proxy and hasattr(auth_strategy, "_proxy"):
                auth_strategy._proxy = self.proxy
            logger.debug("Applying auth strategy: %s", type(auth_strategy).__name__)
            auth_strategy.apply(request, headers)
        except Exception as auth_exc:
            # Redact the exception message — it may contain tokens or passwords
            safe_msg = redact_body(str(auth_exc), max_length=200) or "unknown error"
            logger.error(
                "Authentication failed (%s): %s — %s",
                type(auth_exc).__name__,
                type(auth_strategy).__name__,
                safe_msg,
            )
            # When the failure is caused by a dead proxy, surface that fact
            # directly rather than burying it inside "Authentication failed: …"
            if self.proxy and (
                "10061" in safe_msg
                or "connection refused" in safe_msg.lower()
                or "econnrefused" in safe_msg.lower()
            ):
                raise RequestError(
                    f"OAuth2 token refresh failed — proxy ({self.proxy}) is not reachable. "
                    "Please check your proxy settings under Preferences.",
                    details={"proxy": self.proxy},
                )
            raise RequestError(f"Authentication failed: {safe_msg}")

        # Identify which headers were added by the auth strategy
        auth_headers = {k: headers[k] for k in headers if k not in snapshot}
        if auth_headers:
            logger.debug(
                "Auth applied (%s): %s",
                type(auth_strategy).__name__,
                ", ".join(auth_headers.keys()),
            )
        return auth_headers

    def _dispatch_request(
        self,
        request: Request,
        headers: Dict[str, str],
        auth_headers: Optional[Dict[str, str]] = None,
    ) -> Response:
        """Choose the right httpx client (cert-aware or standard) and execute the request."""
        cert_path = getattr(request, "cert_path", None)
        if cert_path:
            logger.debug("_dispatch_request: using client certificate at %s", cert_path)
            return self._execute_with_client_certificate(request, headers, cert_path, auth_headers)
        logger.debug("_dispatch_request: using standard httpx client")
        return self._execute_httpx(self._client, request, headers, auth_headers)

    def _execute_with_client_certificate(
        self,
        request: Request,
        headers: Dict[str, str],
        cert_path: str,
        auth_headers: Optional[Dict[str, str]] = None,
    ) -> Response:
        """Execute the request using a per-request httpx.Client with a client certificate.

        A separate client is required because httpx does not support per-call cert overrides.
        """
        cert_key_path = getattr(request, "cert_key_path", None)
        cert_arg = (cert_path, cert_key_path) if cert_key_path else cert_path
        logger.debug(
            "_execute_with_client_certificate: creating cert client for %s (key=%s)",
            cert_path, cert_key_path or "none",
        )

        with httpx.Client(
            timeout=self.timeout,
            follow_redirects=self.follow_redirects,
            verify=self._build_ssl_context(),
            proxy=self.proxy,
            cert=cert_arg,
            cookies=self._get_current_cookies(),
        ) as cert_client:
            logger.debug("Cert client created successfully, executing request")
            response = self._execute_httpx(cert_client, request, headers, auth_headers)
            logger.debug("Request completed, cert client will be closed on context exit")
            return response

    def _update_cookie_jar(self, request: Request, response: Optional[Response]) -> None:
        """Persist any Set-Cookie headers from the response into the cookie manager."""
        if self._cookie_manager is None or response is None:
            logger.debug("_update_cookie_jar: no cookie manager or response, skipping")
            return
        try:
            response_headers = dict(response.headers) if response else {}
            if response_headers.get("set-cookie"):
                logger.debug("Updating cookie jar from Set-Cookie header")
                self._cookie_manager.update_from_response(response_headers, request.url)
                logger.debug("Cookie jar updated successfully")
            else:
                logger.debug("No Set-Cookie header in response")
        except Exception as cookie_exc:
            logger.debug("Cookie jar update failed: %s", cookie_exc)

    def _handle_error(
        self,
        request: Request,
        error: Exception,
        audit_tag: Optional[str] = None,
        log_message: Optional[str] = None,
    ) -> Optional[Response]:
        """Pass an error through the interceptor chain.

        If an interceptor suppresses the error (returns None), this method returns None.
        Otherwise it re-raises the (potentially transformed) error.
        """
        if audit_tag:
            logger.debug("Logging error to audit trail: tag=%s", audit_tag)
            self._audit.log_request(request.method, request.url, error=audit_tag)

        if log_message:
            logger.warning("Error log message: %s", log_message)

        logger.debug("Processing error through interceptor chain: type=%s", type(error).__name__)
        processed_error = self.interceptors.process_error(request, error)
        if processed_error is not None:
            logger.debug("Interceptor returned error, re-raising: %s", type(processed_error).__name__)
            raise processed_error
        logger.debug("Interceptor suppressed error, returning None")
        return None

    def _execute_httpx(
        self,
        client: httpx.Client,
        request: Request,
        headers: Dict[str, str],
        auth_headers: Optional[Dict[str, str]] = None,
    ) -> Response:
        """Execute the httpx request and build a Response object.

        When *auth_headers* is provided, they are passed through httpx's
        native ``auth`` parameter via :class:`_RedirectSafeAuth` so the
        ``Authorization`` header survives cross-origin redirects (httpx
        strips manual ``Authorization`` headers on redirect by default).
        """
        logger.debug(
            "Sending %s request to %s",
            request.method,
            Validator.sanitize_for_display(request.url, 100),
        )
        start_time = time.time()

        # Pass auth headers through httpx's native auth mechanism so they
        # survive cross-origin redirects (scheme/host/port changes).
        httpx_auth: Optional[_RedirectSafeAuth] = None
        if auth_headers:
            # Remove auth headers from the regular headers dict — they'll
            # be injected by the auth flow on every leg of the redirect chain.
            for key in auth_headers:
                headers.pop(key, None)
            httpx_auth = _RedirectSafeAuth(auth_headers)

        multipart_files, opened_file_handles = self._build_multipart_files(request)
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
                timeout=request.timeout or self.timeout,
                follow_redirects=(
                    request.follow_redirects
                    if request.follow_redirects is not None
                    else self.follow_redirects
                ),
                auth=httpx_auth,
            )
        finally:
            for file_handle in opened_file_handles:
                file_handle.close()

        # Log redirect chain for diagnostics
        if raw.history:
            chain = " → ".join(
                f"{r.status_code} {r.headers.get('location', '?')}"
                for r in raw.history
            )
            logger.info(
                "Request followed %d redirect(s): %s → %d",
                len(raw.history), chain, raw.status_code,
            )

        elapsed = time.time() - start_time
        logger.debug(
            "Request completed in %.2fs with status %d", elapsed, raw.status_code
        )
        return Response(
            status_code=raw.status_code,
            reason=self._extract_reason_phrase(raw),
            headers=dict(raw.headers),
            body=raw.content,
            elapsed=elapsed,
            request=request,
            timestamp=utc_now(),
            sent_headers=dict(raw.request.headers),
            sent_url=str(raw.request.url),
            timings={"total_ms": round(elapsed * 1000, 2)},
        )

    @staticmethod
    def _extract_reason_phrase(raw_response: httpx.Response) -> str:
        """Extract the reason phrase from the raw httpx response, with a fallback.

        When ``reason_phrase`` is ``None`` (e.g. HTTP/2), attempt to extract
        a ``statusText`` key from the response body — but only from the
        already-consumed content to avoid double-reading the stream.
        """
        reason = raw_response.reason_phrase
        if reason is None:
            try:
                content_object = raw_response.json()
                if isinstance(content_object, dict) and "statusText" in content_object:
                    reason = content_object["statusText"]
            except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
                pass
        return reason or ""

    def _build_multipart_files(
        self,
        request: Request,
    ) -> Tuple[Optional[List[Tuple[str, Any]]], List]:
        """Build the httpx ``files`` list from multipart_data, opening file handles.

        Returns a list of ``(field_name, (filename, data))`` tuples rather
        than a dict so that multiple fields with the **same key** are
        preserved (common in file-upload forms).

        Returns:
            (multipart_files_list_or_None, list_of_opened_file_handles)
            The caller is responsible for closing all returned file handles.
        """
        # Delegate to the centralized multipart builder helper
        from equinox.core.multipart import build_multipart_files

        multipart_data = getattr(request, "multipart_data", None)
        return build_multipart_files(multipart_data)

    def get(self, url: str, **kwargs) -> Response:
        """Convenience method for GET request"""
        request = Request(method="GET", url=url, **kwargs)
        return self.send(request)

    def post(self, url: str, body: Optional[str] = None, **kwargs) -> Response:
        """Convenience method for POST request"""
        request = Request(method="POST", url=url, body=body, **kwargs)
        return self.send(request)

    def put(self, url: str, body: Optional[str] = None, **kwargs) -> Response:
        """Convenience method for PUT request"""
        request = Request(method="PUT", url=url, body=body, **kwargs)
        return self.send(request)

    def patch(self, url: str, body: Optional[str] = None, **kwargs) -> Response:
        """Convenience method for PATCH request"""
        request = Request(method="PATCH", url=url, body=body, **kwargs)
        return self.send(request)

    def delete(self, url: str, **kwargs) -> Response:
        """Convenience method for DELETE request"""
        request = Request(method="DELETE", url=url, **kwargs)
        return self.send(request)

    def head(self, url: str, **kwargs) -> Response:
        """Convenience method for HEAD request"""
        request = Request(method="HEAD", url=url, **kwargs)
        return self.send(request)

    def options(self, url: str, **kwargs) -> Response:
        """Convenience method for OPTIONS request"""
        request = Request(method="OPTIONS", url=url, **kwargs)
        return self.send(request)
