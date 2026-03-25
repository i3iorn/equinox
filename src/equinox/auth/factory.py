"""Auth factory helpers for deserializing auth objects from dicts.

Centralizes the mapping from auth_type string to the appropriate
``from_dict`` constructor to avoid duplicated branching across the codebase.
"""
import logging
from typing import Any, Optional, Dict

logger = logging.getLogger(__name__)


def auth_from_dict(auth_type: str, data: Dict[str, Any]) -> Optional[Any]:
    """Return an auth object reconstructed from *auth_type* and *data*.

    Returns None if the type is unknown or reconstruction fails.
    """
    try:
        if auth_type == "OAuth2Auth":
            from equinox.auth.oauth2 import OAuth2Auth

            return OAuth2Auth.from_dict(data)
        if auth_type == "BasicAuth":
            from equinox.auth.basic import BasicAuth

            return BasicAuth.from_dict(data)
        if auth_type == "BearerAuth":
            from equinox.auth.bearer import BearerAuth

            return BearerAuth.from_dict(data)
        if auth_type == "APIKeyAuth":
            from equinox.auth.api_key import APIKeyAuth

            return APIKeyAuth.from_dict(data)
        logger.warning("Unknown auth type in auth_from_dict: %s", auth_type)
    except Exception as exc:
        logger.error("Failed to reconstruct auth %s: %s", auth_type, exc)
    return None

