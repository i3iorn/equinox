"""Centralized mapping of transport/library exceptions to Equinox errors.

This module contains helpers to detect SSL vs proxy vs generic connection
failures and returns structured dicts compatible with the existing
HTTPClient._error_handlers entries.
"""

import ssl
import logging
from typing import Any, Dict, Optional

from equinox.core.exceptions import CertificateError, RequestError, RequestTimeoutError
from equinox.core.redact import redact_url

logger = logging.getLogger(__name__)


def _is_ssl_error(exc: Exception) -> bool:
    """Return True if *exc* wraps an SSL failure.

    Walks the __cause__ / __context__ chain and also falls back to
    checking the exception message for SSL-related keywords.
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
    msg = str(exc).lower()
    return "ssl" in msg or "certificate" in msg


def _is_proxy_error(exc: Exception) -> bool:
    """Detect whether *exc* originated from an http-proxy connection attempt.

    We heuristically inspect traceback frames for httpcore's proxy module
    filenames. This follows the original implementation in HTTPClient.
    """
    seen: set = set()
    stack = [exc]
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
        if cause is not None:
            stack.append(cause)
        context = getattr(e, "__context__", None)
        if context is not None:
            stack.append(context)
    return False


def build_error_handlers(client) -> list:
    """Return the list of (exc_type, handler_fn) entries for the HTTPClient.

    The returned handlers are compatible with the existing client contract:
    handler_fn(exc, req) -> dict with keys: error, log_message, optional audit_tag
    """
    import httpx

    def _connect_handler(exc: Exception, req: Any) -> Dict[str, Any]:
        if _is_ssl_error(exc):
            return dict(
                error=CertificateError(
                    "SSL certificate verification failed. The server's certificate is invalid or untrusted.",
                    details={"url": redact_url(req.url)},
                ),
                log_message=(f"SSL certificate verification failed for {redact_url(req.url)}" + (f": {exc}" if str(exc) else "")),
            )

        if client.proxy and _is_proxy_error(exc):
            return dict(
                error=RequestError(
                    f"Failed to connect to proxy ({client.proxy}). Please check your proxy settings under Preferences.",
                    details={"url": redact_url(req.url), "proxy": client.proxy},
                ),
                log_message=(f"Proxy connection error ({client.proxy})" + (f": {exc}" if str(exc) else "") + f" — for {redact_url(req.url)}"),
            )

        return dict(
            error=RequestError(
                "Failed to connect to server. Please check the URL and your network connection.",
                details={"url": redact_url(req.url)},
            ),
            log_message=(f"Connection error for {redact_url(req.url)}" + (f": {exc}" if str(exc) else "")),
        )

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
                error=RequestTimeoutError(f"Request timed out after {client.timeout} seconds", details={"url": redact_url(req.url), "timeout": client.timeout}),
                audit_tag="timeout",
                log_message=f"Request timeout after {client.timeout}s for {redact_url(req.url)}",
            ),
        ),
        (
            httpx.ConnectError,
            _connect_handler,
        ),
        (
            httpx.TooManyRedirects,
            lambda exc, req: dict(
                error=RequestError(f"Too many redirects (max: {client.MAX_REDIRECTS})", details={"url": redact_url(req.url)}),
                log_message=f"Too many redirects for {redact_url(req.url)}",
            ),
        ),
        (
            httpx.HTTPStatusError,
            lambda exc, req: dict(
                error=RequestError(f"HTTP error: {exc.response.status_code}", details={"url": redact_url(req.url), "status": exc.response.status_code}),
                log_message=f"HTTP error status {exc.response.status_code} for {redact_url(req.url)}",
            ),
        ),
        (
            httpx.HTTPError,
            lambda exc, req: dict(
                error=RequestError("HTTP request failed", details={"url": redact_url(req.url)}),
                log_message=f"HTTP error for {redact_url(req.url)}",
            ),
        ),
        (
            UnicodeEncodeError,
            lambda exc, req: dict(
                error=RequestError("Request body contains invalid characters", details={}),
                log_message="Encoding error in request body",
            ),
        ),
    ]

