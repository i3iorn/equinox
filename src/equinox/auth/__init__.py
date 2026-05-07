"""Authentication strategies and schemes for HTTP requests.

This package provides authentication implementations for making authenticated
HTTP requests. Equinox automatically handles credential injection, token
refresh (for OAuth2), and secure storage of sensitive values.

Supported Authentication Types
==============================

- :class:`BearerAuth`
  Token-based authentication (OAuth2 bearer tokens, API tokens, JWTs).
  Use when you have a bearer token to inject in the Authorization header.

- :class:`BasicAuth`
  HTTP Basic Authentication (RFC 7617). Use for username/password credentials
  that are base64-encoded in the Authorization header.

- :class:`APIKeyAuth`
  API key-based authentication. Supports header injection (default), query
  parameter, or path parameter placement.

- :class:`OAuth2Auth`
  OAuth 2.0 with automatic token refresh. Handles token lifecycle,
  expiration detection, and automatic refresh. Encrypted token storage.

- :class:`AWSSigV4Auth`
  AWS Signature Version 4 request signing. Use for AWS service authentication.

- :class:`AuthStrategy`
  Abstract base class for implementing custom authentication schemes.

Quick Start
===========

Direct Use::

    from equinox.auth import BearerAuth, OAuth2Auth

    auth = BearerAuth(token="my-token")
    response = client.send(request, auth=auth)

Factory Pattern (Deserialization)::

    from equinox.auth import auth_from_dict, get_auth_type, list_auth_types

    types = list_auth_types()  # ['api_key', 'basic', 'bearer', 'oauth2', 'aws_sigv4']
    auth_dict = {"type": "bearer", "token": "..."}
    auth = auth_from_dict(auth_dict)

Custom Authentication::

    from equinox.auth import AuthStrategy

    class MyCustomAuth(AuthStrategy):
        AUTH_TYPE = "custom"
        DISPLAY_NAME = "My Custom Auth"

        def apply(self, request):
            request.headers["X-Custom"] = "value"
            return request

See Also
========

- :mod:`equinox.auth.base` — Base class and protocol definitions
- :mod:`equinox.auth.factory` — Auth type registration and deserialization
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Type

from equinox.auth._base import AuthStrategy
from equinox.auth._bearer import BearerAuth
from equinox.auth._api_key import APIKeyAuth
from equinox.auth._basic import BasicAuth
from equinox.auth._oauth2 import OAuth2Auth
from equinox.auth._aws_sigv4 import AWSSigV4Auth

if TYPE_CHECKING:
    from equinox.auth._factory import AUTH_REGISTRY

__all__ = [
    "AuthStrategy",
    "BearerAuth",
    "APIKeyAuth",
    "BasicAuth",
    "OAuth2Auth",
    "AWSSigV4Auth",
    "auth_from_dict",
    "get_auth_type",
    "list_auth_types",
]


def auth_from_dict(data: Dict[str, Any]) -> AuthStrategy:
    """Deserialize an AuthStrategy from a dictionary.

    Args:
        data: Dictionary with "type" key and auth-specific fields.

    Returns:
        Deserialized AuthStrategy instance

    Raises:
        ValueError: If auth type is unknown
    """
    from equinox.auth._factory import auth_from_dict as _auth_from_dict

    auth_type = data["type"]
    auth = _auth_from_dict(auth_type, data)
    if auth is None:
        raise ValueError(f"Failed to deserialize auth type: {auth_type!r}")
    return auth


def get_auth_type(name: str) -> Type[AuthStrategy]:
    """Get an auth strategy class by type name.

    Supports short names ("bearer") and class names ("BearerAuth").

    Args:
        name: Auth type identifier.

    Returns:
        Auth strategy class (not an instance)

    Raises:
        ValueError: If type name is unknown
    """
    from equinox.auth._factory import AUTH_REGISTRY

    if name in AUTH_REGISTRY:
        loader = AUTH_REGISTRY[name]
        return loader()

    for loader in AUTH_REGISTRY.values():
        cls = loader()
        if cls.__name__ == name:
            return cls

    available = sorted(AUTH_REGISTRY.keys())
    raise ValueError(f"Unknown auth type: {name!r}. Available: {available}")


def list_auth_types() -> List[str]:
    """List all available auth type identifiers.

    Returns:
        Sorted list of auth type names.
    """
    from equinox.auth._factory import AUTH_REGISTRY

    return sorted(AUTH_REGISTRY.keys())


def _validate_all_exports() -> None:
    """Validate that all names in __all__ are actually exported."""
    import sys

    module = sys.modules[__name__]
    missing = [name for name in __all__ if not hasattr(module, name)]

    if missing:
        raise ImportError(
            f"Module equinox.auth exports {missing!r} in __all__ "
            f"but they are not defined."
        )


_validate_all_exports()
del _validate_all_exports

