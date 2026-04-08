"""Rich error enrichment for user-facing exception messages.

Converts raw exceptions (httpx, equinox, stdlib) into structured
``RichError`` objects with human-readable messages, exception type
labels, and full tracebacks for logging.
"""

import dataclasses
import logging
import traceback
from typing import Optional

from equinox.core.redact import redact_body as _redact, redact_url as _redact_url

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class RichError:
    """Structured error with a meaningful, user-facing message
    plus a full log-level traceback."""

    exc_type: str       # e.g. "ConnectError", "TimeoutError"
    message: str        # Human-readable, never empty
    tb: str             # Full traceback string for the log file


def enrich_exception(exc: Exception) -> RichError:
    """Convert any exception into a *RichError* with a descriptive message.

    Many httpx / equinox exceptions have useful ``details`` dicts or are
    best described by their *type* rather than their (often empty) message.
    """

    exc_type = type(exc).__name__
    tb = traceback.format_exc()

    # Log full traceback regardless — redact secrets that might appear
    logger.debug("Exception in worker thread:\n%s", _redact(tb))

    raw = str(exc).strip()

    msg = _enrich_httpx_error(exc, raw, exc_type)
    if msg is None:
        msg = _enrich_equinox_error(exc, raw, exc_type)
    if msg is None:
        msg = raw or f"Unexpected error ({exc_type})"

    # Scrub any credential fragments that leaked through exception strings
    return RichError(exc_type=exc_type, message=_redact(msg), tb=_redact(tb))


def _enrich_httpx_error(exc: Exception, raw: str, exc_type: str) -> "str | None":
    """Return a human-readable message for httpx errors, or None."""
    import httpx

    if isinstance(exc, httpx.ConnectTimeout):
        return "Connection timed out — the server did not respond in time."
    if isinstance(exc, httpx.ReadTimeout):
        return "Server took too long to send a response (read timeout)."
    if isinstance(exc, httpx.ConnectError):
        if _is_proxy_connect_error(exc):
            return (
                "Failed to connect to the proxy server. "
                "Check your proxy settings under Preferences."
            )
        return _describe_connect_error(str(exc))
    if isinstance(exc, httpx.TooManyRedirects):
        return "Too many redirects — the server may be redirecting in a loop."
    if isinstance(exc, httpx.TimeoutException):
        return f"Request timed out ({exc_type})."
    if isinstance(exc, httpx.HTTPStatusError):
        return f"Server returned HTTP {exc.response.status_code}."
    if isinstance(exc, httpx.InvalidURL):
        return f"Invalid URL: {exc}"
    if isinstance(exc, httpx.HTTPError):
        return f"HTTP error: {raw or exc_type}"
    return None


def _describe_connect_error(inner: str) -> str:
    """Produce a helpful message for an httpx.ConnectError."""
    # Sanitize the inner message — it may contain URLs with embedded credentials
    inner = _redact_url(inner)
    lower = inner.lower()
    if "ssl" in lower or "certificate" in lower:
        return (
            "SSL/TLS error — the server's certificate could not be verified.\n"
            f"Details: {inner}"
        )
    if "name or service not known" in lower or "nodename nor servname" in lower or "getaddrinfo failed" in lower:
        return "DNS lookup failed — check the hostname in the URL."
    if "connection refused" in lower:
        return "Connection refused — the server is not accepting connections on that port."
    return f"Could not connect to server.\n{inner or '(no additional detail)'}"


def _is_proxy_connect_error(exc: Exception) -> bool:
    """Return True if any frame in the full exception graph is from httpcore's proxy module.

    Follows both ``__cause__`` and ``__context__`` — ``raise exc from None``
    clears ``__cause__`` but leaves ``__context__`` intact.
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


def _enrich_equinox_error(exc: Exception, raw: str, exc_type: str) -> Optional[str]:
    """Return a human-readable message for equinox domain errors, or None."""
    from equinox.core.exceptions import (
        TimeoutError as EqTimeoutError,
        RequestError, ValidationError, AuthError,
    )

    if isinstance(exc, EqTimeoutError):
        details = getattr(exc, "details", {})
        timeout = details.get("timeout", "")
        return f"Request timed out{f' after {timeout}s' if timeout else ''}."
    if isinstance(exc, AuthError):
        return f"Authentication failed: {raw or 'check your credentials.'}"
    if isinstance(exc, ValidationError):
        return f"Validation error: {raw}"
    if isinstance(exc, RequestError):
        return raw or f"Request failed ({exc_type})"
    return None

