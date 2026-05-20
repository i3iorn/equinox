"""Auth factory — single source of truth for auth-type registration.

The :data:`AUTH_REGISTRY` maps every known type identifier to a lazy-import
function that returns the class.  Both ``auth_from_dict`` and the storage
layer use this registry so a new auth type only needs to be registered here.

The registry accepts two keys per type:

- The short ``to_dict()["type"]`` name (``"bearer"``, ``"basic"``, …).
- The class name string (``"BearerAuth"``, ``"BasicAuth"``, …).

Display names and labels are derived from the classes themselves
(``cls.DISPLAY_NAME``, ``cls.AUTH_TYPE``), eliminating parallel constants.
"""

import logging
from typing import Any, Callable, Optional, Tuple, Type, cast

from equinox.auth._base import AuthStrategy
from equinox.core.exceptions import AuthError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lazy-import helpers (avoid circular imports at module level)
# ---------------------------------------------------------------------------


def _get_bearer() -> Type[AuthStrategy]:
    from equinox.auth._bearer import BearerAuth

    return cast(Type[AuthStrategy], BearerAuth)


def _get_basic() -> Type[AuthStrategy]:
    from equinox.auth._basic import BasicAuth

    return cast(Type[AuthStrategy], BasicAuth)


def _get_api_key() -> Type[AuthStrategy]:
    from equinox.auth._api_key import APIKeyAuth

    return cast(Type[AuthStrategy], APIKeyAuth)


def _get_oauth2() -> Type[AuthStrategy]:
    from equinox.auth._oauth2 import OAuth2Auth

    return cast(Type[AuthStrategy], OAuth2Auth)


def _get_aws_sigv4() -> Type[AuthStrategy]:
    from equinox.auth._aws_sigv4 import AWSSigV4Auth

    return cast(Type[AuthStrategy], AWSSigV4Auth)


# ---------------------------------------------------------------------------
# Unified registry: maps every known type identifier → lazy class loader
# ---------------------------------------------------------------------------

AUTH_REGISTRY: dict[str, Callable[[], Type[AuthStrategy]]] = {
    # Short names used in to_dict()["type"]
    "bearer": _get_bearer,
    "basic": _get_basic,
    "api_key": _get_api_key,
    "oauth2": _get_oauth2,
    "aws_sigv4": _get_aws_sigv4,
    # Class names used in Request.to_dict()["auth_type"]
    "BearerAuth": _get_bearer,
    "BasicAuth": _get_basic,
    "APIKeyAuth": _get_api_key,
    "OAuth2Auth": _get_oauth2,
    "AWSSigV4Auth": _get_aws_sigv4,
}

# Canonical ordering for UI display (tab order, picker order).
# Each entry is a short type name that can be resolved via AUTH_REGISTRY.
AUTH_TYPE_ORDER: Tuple[str, ...] = ("basic", "bearer", "oauth2", "api_key", "aws_sigv4")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def auth_from_dict(*args: object, **kwargs: Any) -> Optional[AuthStrategy]:
    """Return an auth object reconstructed from *auth_type* and *data*.

    Accepts both short type names (``"bearer"``) and class names
    (``"BearerAuth"``).  Returns ``None`` if reconstruction fails.

    Raises:
        ValueError: If the type is not in :data:`AUTH_REGISTRY`.
    """
    data: dict[str, Any]
    if len(args) == 1:
        arg = args[0]
        if isinstance(arg, str):
            auth_type = arg
            candidate = kwargs.get("data")
            if not isinstance(candidate, dict):
                raise AuthError(
                    "Invalid arguments to auth_from_dict: missing 'data' when called as (auth_type)"
                )
            data = candidate
        elif isinstance(arg, dict):
            auth_type = arg["type"]
            data = arg
        else:
            raise AuthError(
                f"Invalid arguments to auth_from_dict: expected (auth_type) or (data)\ngot {type(args)}"
            )
    elif len(args) == 2:
        raw_auth_type, raw_data = args
        if not isinstance(raw_auth_type, str) or not isinstance(raw_data, dict):
            raise AuthError("Invalid arguments to auth_from_dict: expected (auth_type, data)")
        auth_type = raw_auth_type
        data = raw_data
    else:
        raise AuthError("Invalid arguments to auth_from_dict: expected (auth_type) or (data)")

    loader = AUTH_REGISTRY.get(auth_type)
    if loader is None:
        logger.warning("Unknown auth type in auth_from_dict: %s", auth_type)
        raise ValueError(f"Unknown auth type: {auth_type}")
    try:
        cls = loader()
        return cls.from_dict(data, **kwargs)
    except Exception as exc:
        logger.error("Failed to reconstruct auth %s: %s", auth_type, exc)
    return None


def get_auth_class(auth_type: str) -> Optional[Type[AuthStrategy]]:
    """Return the auth class for *auth_type*, or ``None`` if unknown."""
    loader = AUTH_REGISTRY.get(auth_type)
    if loader is None:
        return None
    return loader()


def get_auth_type_labels() -> dict[str, str]:
    """Return ``{auth_type: display_name}`` for all registered types.

    Derived from each class's ``DISPLAY_NAME`` attribute — no separate
    constant to maintain.
    """
    labels: dict[str, str] = {}
    for short_name in AUTH_TYPE_ORDER:
        loader = AUTH_REGISTRY.get(short_name)
        if loader:
            cls = loader()
            labels[short_name] = getattr(cls, "DISPLAY_NAME", short_name)
    return labels


def get_auth_types() -> Tuple[str, ...]:
    """Return the canonical tuple of auth-type short names."""
    return AUTH_TYPE_ORDER
