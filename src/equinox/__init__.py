"""
Equinox - A local-first API testing tool
"""

__version__ = "0.4.5"
__author__ = "Björn Schrammel"

from equinox.core.client import HTTPClient
from equinox.core.request import Request, Response
from equinox.auth import AuthStrategy, BearerAuth, APIKeyAuth, BasicAuth
from . import application

__all__ = [
    "HTTPClient",
    "Request",
    "Response",
    "AuthStrategy",
    "BearerAuth",
    "APIKeyAuth",
    "BasicAuth",
    "application",
]
