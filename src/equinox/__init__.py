"""
Equinox - A local-first API testing tool
"""

__version__ = "0.4.9"
__author__ = "Björn Schrammel"

from equinox.auth import APIKeyAuth, AuthStrategy, BasicAuth, BearerAuth
from equinox.core.client import HTTPClient
from equinox.core.request import Request, Response

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
