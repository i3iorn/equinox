"""Authentication strategies for HTTP requests"""

from equinox.auth.base import AuthStrategy
from equinox.auth.bearer import BearerAuth
from equinox.auth.api_key import APIKeyAuth
from equinox.auth.basic import BasicAuth
from equinox.auth.oauth2 import OAuth2Auth
from equinox.auth.aws_sigv4 import AWSSigV4Auth

__all__ = [
    "AuthStrategy",
    "BearerAuth",
    "APIKeyAuth",
    "BasicAuth",
    "OAuth2Auth",
    "AWSSigV4Auth",
]
