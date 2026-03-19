"""Core HTTP client and request handling"""
from datetime import datetime, timezone
from typing import Optional

from equinox.core.client import HTTPClient
from equinox.core.request import Request, Response
from equinox.core.exceptions import EquinoxError, RequestError, AuthError, RequestTimeoutError

def utc_now(ts: Optional[datetime]) -> datetime:
    """Return the current UTC time as a naive datetime (no tzinfo)."""
    tzinfo = None
    if ts:
        tzinfo = ts.astimezone(timezone.utc)
    return datetime.now(timezone.utc).replace(tzinfo=tzinfo)

__all__ = [
    "HTTPClient", "Request", "Response",
    "EquinoxError", "RequestError", "AuthError", "RequestTimeoutError",
]
