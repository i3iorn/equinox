"""
Equinox - A local-first API testing tool
"""

__version__ = "0.1.0"
__author__ = "Equinox Team"

from equinox.core.client import HTTPClient
from equinox.core.request import Request, Response
from equinox.auth import AuthStrategy, BearerAuth, APIKeyAuth, BasicAuth

__all__ = [
    "HTTPClient",
    "Request",
    "Response",
    "AuthStrategy",
    "BearerAuth",
    "APIKeyAuth",
    "BasicAuth",
]
