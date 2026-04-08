"""Auth factory helpers for deserializing auth objects from dicts.

Centralizes the mapping from auth_type string to the appropriate
``from_dict`` constructor to avoid duplicated branching across the codebase.

The :data:`AUTH_REGISTRY` maps **all** known type identifiers — both the
short ``to_dict()["type"]`` names (``"bearer"``, ``"basic"``, …) and the
class-name strings (``"BearerAuth"``, ``"BasicAuth"``, …) — to a lazy
import function that returns the class.  This single registry is used by
:func:`auth_from_dict` *and* by the storage-layer deserializer so a new
auth type only needs to be registered in one place.
"""
import logging
from typing import Any, Callable, Dict, Optional, Type

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lazy-import helpers (avoid circular imports at module level)
# ---------------------------------------------------------------------------

def _get_bearer() -> Type:
    from equinox.auth.bearer import BearerAuth
    return BearerAuth


def _get_basic() -> Type:
    from equinox.auth.basic import BasicAuth
    return BasicAuth


def _get_api_key() -> Type:
    from equinox.auth.api_key import APIKeyAuth
    return APIKeyAuth


def _get_oauth2() -> Type:
    from equinox.auth.oauth2 import OAuth2Auth
    return OAuth2Auth


def _get_aws_sigv4() -> Type:
    from equinox.auth.aws_sigv4 import AWSSigV4Auth
    return AWSSigV4Auth


# ---------------------------------------------------------------------------
# Unified registry: maps every known type identifier → lazy class loader
# ---------------------------------------------------------------------------

AUTH_REGISTRY: Dict[str, Callable[[], Type]] = {
    # Short names used in to_dict()["type"]
    "bearer":    _get_bearer,
    "basic":     _get_basic,
    "api_key":   _get_api_key,
    "oauth2":    _get_oauth2,
    "aws_sigv4": _get_aws_sigv4,
    # Class names used in Request.to_dict()["auth_type"]
    "BearerAuth":   _get_bearer,
    "BasicAuth":    _get_basic,
    "APIKeyAuth":   _get_api_key,
    "OAuth2Auth":   _get_oauth2,
    "AWSSigV4Auth": _get_aws_sigv4,
}


def auth_from_dict(auth_type: str, data: Dict[str, Any]) -> Optional[Any]:
    """Return an auth object reconstructed from *auth_type* and *data*.

    Accepts both short type names (``"bearer"``) and class names
    (``"BearerAuth"``).  Returns ``None`` if the type is unknown or
    reconstruction fails.
    """
    loader = AUTH_REGISTRY.get(auth_type)
    if loader is None:
        logger.warning("Unknown auth type in auth_from_dict: %s", auth_type)
        raise ValueError(f"Unknown auth type: {auth_type}")
    try:
        cls = loader()
        return cls.from_dict(data)
    except Exception as exc:
        logger.error("Failed to reconstruct auth %s: %s", auth_type, exc)
    return None

