"""Centralized mapping of transport/library exceptions to Equinox errors.

This module contains helpers to detect SSL vs proxy vs generic connection
failures and returns structured dicts compatible with the existing
HTTPClient._error_handlers entries.
"""

import logging
import ssl
from typing import Any, Optional

from equinox.core.exceptions import CertificateError, RequestError, RequestTimeoutError
from equinox.security import redact_url

logger = logging.getLogger(__name__)


def _url_str(req: Any) -> str:
    """Return redacted URL string for error/log payloads."""
    return redact_url(req.url) or ""


def _suffix(exc: Exception) -> str:
    """Return ': <exc>' suffix only when exception text is available."""
    return f": {exc}" if str(exc) else ""


def _is_ssl_error(exc: BaseException) -> bool:
    """Return True if *exc* wraps an SSL failure.

    Walks the __cause__ / __context__ chain and also falls back to
    checking the exception message for SSL-related keywords.
    """
    seen = set()
    stack: list[BaseException] = [exc]
    while stack:
        e = stack.pop()
        eid = id(e)
        if eid in seen:
            continue
        seen.add(eid)
        if isinstance(e, ssl.SSLError):
            return True
        cause = getattr(e, "__cause__", None)
        if isinstance(cause, BaseException):
            stack.append(cause)
        context = getattr(e, "__context__", None)
        if isinstance(context, BaseException):
            stack.append(context)
    msg = str(exc).lower()
    return "ssl" in msg or "certificate" in msg


def _is_proxy_error(exc: BaseException) -> bool:
    """Detect whether *exc* originated from an http-proxy connection attempt.

    We heuristically inspect traceback frames for httpcore's proxy module
    filenames. This follows the original implementation in HTTPClient.
    """
    seen = set()
    stack: list[BaseException] = [exc]
    while stack:
        e = stack.pop()
        eid = id(e)
        if eid in seen:
            continue
        seen.add(eid)
        tb = getattr(e, "__traceback__", None)
        while tb is not None:
            if "http_proxy" in (tb.tb_frame.f_code.co_filename or ""):
                return True
            tb = tb.tb_next
        cause = getattr(e, "__cause__", None)
        if isinstance(cause, BaseException):
            stack.append(cause)
        context = getattr(e, "__context__", None)
        if isinstance(context, BaseException):
            stack.append(context)
    return False


def _connect_timeout_handler(exc: Exception, req: Any) -> dict[str, Any]:
    """Handle connect timeout errors."""
    url = _url_str(req)
    return dict(
        error=RequestTimeoutError(
            "Connection timed out",
            details={"url": url},
            hint_key="timeout",
        ),
        log_message=f"Connection timeout for {url}",
    )


def _read_timeout_handler(exc: Exception, req: Any) -> dict[str, Any]:
    """Handle read timeout errors."""
    url = _url_str(req)
    return dict(
        error=RequestTimeoutError(
            "Server response timed out",
            details={"url": url},
            hint_key="timeout",
        ),
        log_message=f"Read timeout for {url}",
    )


def _timeout_handler_factory(timeout: float) -> Any:
    """Build generic timeout handler with configured timeout value."""

    def _handler(exc: Exception, req: Any) -> dict[str, Any]:
        url = _url_str(req)
        return dict(
            error=RequestTimeoutError(
                f"Request timed out after {timeout} seconds",
                details={"url": url, "timeout": timeout},
                hint_key="timeout",
            ),
            audit_tag="timeout",
            log_message=f"Request timeout after {timeout}s for {url}",
        )

    return _handler


def _connect_handler_factory(proxy: Optional[str]) -> Any:
    """Build connect error handler that distinguishes SSL/proxy/generic cases."""

    def _handler(exc: Exception, req: Any) -> dict[str, Any]:
        url = _url_str(req)
        if _is_ssl_error(exc):
            return dict(
                error=CertificateError(
                    "SSL certificate verification failed. The server's certificate is invalid or untrusted.",
                    details={"url": url},
                    hint_key="ssl_verify",
                ),
                log_message=f"SSL certificate verification failed for {url}{_suffix(exc)}",
            )

        if proxy and _is_proxy_error(exc):
            return dict(
                error=RequestError(
                    f"Failed to connect to proxy ({proxy}). Please check your proxy settings under Preferences.",
                    details={"url": url, "proxy": proxy},
                    hint_key="connection",
                ),
                log_message=f"Proxy connection error ({proxy}){_suffix(exc)} — for {url}",
            )

        return dict(
            error=RequestError(
                "Failed to connect to server. Please check the URL and your network connection.",
                details={"url": url},
                hint_key="connection",
            ),
            log_message=f"Connection error for {url}{_suffix(exc)}",
        )

    return _handler


def _too_many_redirects_handler_factory(max_redirects: int) -> Any:
    """Build too-many-redirects handler with max redirects value."""

    def _handler(exc: Exception, req: Any) -> dict[str, Any]:
        url = _url_str(req)
        return dict(
            error=RequestError(
                f"Too many redirects (max: {max_redirects})",
                details={"url": url},
                hint_key="connection",
            ),
            log_message=f"Too many redirects for {url}",
        )

    return _handler


def _http_status_handler(exc: Any, req: Any) -> dict[str, Any]:
    """Handle HTTP status exceptions."""
    url = _url_str(req)
    status = exc.response.status_code
    return dict(
        error=RequestError(
            f"HTTP error: {status}",
            details={"url": url, "status": status},
        ),
        log_message=f"HTTP error status {status} for {url}",
    )


def _http_error_handler(exc: Exception, req: Any) -> dict[str, Any]:
    """Handle generic HTTP transport errors."""
    url = _url_str(req)
    return dict(
        error=RequestError(
            "HTTP request failed",
            details={"url": url},
            hint_key="connection",
        ),
        log_message=f"HTTP error for {url}",
    )


def _unicode_encode_handler(exc: Exception, req: Any) -> dict[str, Any]:
    """Handle request encoding failures."""
    return dict(
        error=RequestError(
            "Request body contains invalid characters",
            details={},
            hint_key="invalid_json",
        ),
        log_message="Encoding error in request body",
    )


def build_error_handlers(client: Any) -> list[tuple[type[BaseException], Any]]:
    """Return the list of (exc_type, handler_fn) entries for the HTTPClient.

    The returned handlers are compatible with the existing client contract:
    handler_fn(exc, req) -> dict with keys: error, log_message, optional audit_tag
    """
    import httpx

    timeout_handler = _timeout_handler_factory(client.timeout)
    connect_handler = _connect_handler_factory(client.proxy)
    redirects_handler = _too_many_redirects_handler_factory(client.MAX_REDIRECTS)

    return [
        (
            httpx.ConnectTimeout,
            _connect_timeout_handler,
        ),
        (
            httpx.ReadTimeout,
            _read_timeout_handler,
        ),
        (
            httpx.TimeoutException,
            timeout_handler,
        ),
        (
            httpx.ConnectError,
            connect_handler,
        ),
        (
            httpx.TooManyRedirects,
            redirects_handler,
        ),
        (
            httpx.HTTPStatusError,
            _http_status_handler,
        ),
        (
            httpx.HTTPError,
            _http_error_handler,
        ),
        (
            UnicodeEncodeError,
            _unicode_encode_handler,
        ),
    ]
