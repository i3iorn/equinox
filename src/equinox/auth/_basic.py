"""Basic HTTP authentication"""

import base64
import logging
from typing import Any, Dict, Optional
from equinox.auth._base import AuthStrategy, _validate_credential, AuthError

logger = logging.getLogger(__name__)

__all__ = ["BasicAuth"]


class BasicAuth(AuthStrategy):
    """Basic HTTP authentication.

    Encodes ``username:password`` as base-64 and sets the
    ``Authorization: Basic <encoded>`` header.
    """

    AUTH_TYPE = "basic"
    DISPLAY_NAME = "Basic Auth"

    def __init__(self, username: str, password: str):
        """Initialize basic auth.

        Args:
            username: Username
            password: Password

        Raises:
            AuthError: If username or password is empty, too long, or
                contains CRLF characters.
        """
        self.username = _validate_credential(username, "Username")
        self.password = _validate_credential(password, "Password")

    # ── AuthStrategy interface ────────────────────────────────────────────────

    def apply(self, request: Any, headers: Dict[str, str]) -> None:
        """Add Authorization header with basic auth credentials."""
        credentials = f"{self.username}:{self.password}"
        encoded = base64.b64encode(credentials.encode()).decode()
        headers["Authorization"] = f"Basic {encoded}"
        logger.debug("BasicAuth applied for user %r", self.username)

    def to_dict(self) -> Dict[str, Any]:
        return {"type": self.AUTH_TYPE, "username": self.username, "password": self.password}

    @classmethod
    def from_dict(cls, data: Dict[str, Any], **kwargs: Any) -> "BasicAuth":
        """Create from dictionary.

        Raises:
            AuthError: If required keys are missing or values are invalid.
        """
        try:
            return cls(username=data["username"], password=data["password"])
        except KeyError as exc:
            raise AuthError(f"Invalid basic auth data: missing key {exc}") from exc

    # ── Strategy metadata ─────────────────────────────────────────────────────

    def get_display_summary(self) -> str:
        return f"Username: {self.username}"

    def get_preflight_warning(self) -> Optional[str]:
        if not self.username:
            return "Basic auth username is empty"
        return None

    # ── Dunder helpers ────────────────────────────────────────────────────────

    def __repr__(self) -> str:
        masked = f"{self.username[:2]}****" if len(self.username) > 2 else "****"
        return f"BasicAuth(username={masked})"
