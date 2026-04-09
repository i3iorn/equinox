"""Bearer token authentication"""

import logging
from typing import Dict, Any
from equinox.auth.base import AuthStrategy, _validate_credential
from equinox.core.exceptions import AuthError
from equinox.core.redact import mask_secret

logger = logging.getLogger(__name__)


class BearerAuth(AuthStrategy):
    """Bearer token authentication"""

    def __init__(self, token: str):
        """
        Initialize bearer auth

        Args:
            token: Bearer token

        Raises:
            AuthError: If the token is empty, too long, or contains CRLF.
        """
        self.token = _validate_credential(token, "Bearer token")

    def apply(self, request: Any, headers: Dict[str, str]) -> None:
        """Add Authorization header with bearer token"""
        headers["Authorization"] = f"Bearer {self.token}"
        logger.debug("BearerAuth applied (token length: %d)", len(self.token))

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {"type": "bearer", "token": self.token}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BearerAuth":
        """Create from dictionary.

        Raises:
            AuthError: If required keys are missing or values are invalid.
        """
        try:
            return cls(token=data["token"])
        except KeyError as exc:
            raise AuthError(f"Invalid bearer auth data: missing key {exc}") from exc

    def __repr__(self) -> str:
        return f"BearerAuth(token={mask_secret(self.token)})"
