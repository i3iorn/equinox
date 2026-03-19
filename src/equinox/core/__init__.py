"""Core HTTP client and request handling"""
from datetime import datetime, timezone

from equinox.core.client import HTTPClient
from equinox.core.request import Request, Response
from equinox.core.exceptions import EquinoxError, RequestError, AuthError, RequestTimeoutError

def utc_now() -> datetime:
    """Return the current UTC time as a naive datetime (no tzinfo)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)

__all__ = [
    "HTTPClient", "Request", "Response",
    "EquinoxError", "RequestError", "AuthError", "RequestTimeoutError",
]
