"""Bearer token authentication"""

import logging
from typing import Any

from equinox.auth._base import AuthError, AuthStrategy, _validate_credential
from equinox.security import mask_secret

logger = logging.getLogger(__name__)

__all__ = ["BearerAuth"]


class BearerAuth(AuthStrategy):
    """Bearer token authentication strategy.

    Sets the ``Authorization: Bearer <token>`` header on every request.

    Example::

        auth = BearerAuth(token="eyJhbGciOiJIUzI1NiJ9...")
        # Adds:  Authorization: Bearer eyJhbGciOiJIUzI1NiJ9...
    """

    AUTH_TYPE = "bearer"
    DISPLAY_NAME = "Bearer Token"

    def __init__(self, token: str):
        """Initialise bearer auth.

        Args:
            token: Bearer token string.

        Raises:
            AuthError: If *token* is empty, too long, or contains CRLF characters.
        """
        self.token = _validate_credential(token, "Bearer token")

    # ── AuthStrategy interface ────────────────────────────────────────────────

    def apply(self, request: Any, headers: dict[str, str]) -> None:
        """Set ``Authorization: Bearer <token>`` on *headers*."""
        headers["Authorization"] = f"Bearer {self.token}"
        logger.debug("BearerAuth applied (token length: %d)", len(self.token))

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.AUTH_TYPE, "token": self.token}

    @classmethod
    def from_dict(cls, data: dict[str, Any], **kwargs: Any) -> "BearerAuth":
        """Create from a serialised dictionary.

        Raises:
            AuthError: If required keys are missing or values are invalid.
        """
        try:
            return cls(token=data["token"])
        except KeyError as exc:
            raise AuthError(
                f"Invalid {cls.__name__} data: missing key {exc}.\nPresent keys {list(data.keys())}",
            ) from exc

    # ── Strategy metadata ─────────────────────────────────────────────────────

    def get_display_summary(self) -> str:
        return f"Token: {mask_secret(self.token)}"

    def get_preflight_warning(self) -> str | None:
        if not self.token:
            return "Bearer token is empty"
        return None

    # ── Dunder helpers ────────────────────────────────────────────────────────

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, BearerAuth):
            return NotImplemented
        return self.token == other.token

    def __hash__(self) -> int:
        return hash(self.token)

    def __repr__(self) -> str:
        return f"BearerAuth(token={mask_secret(self.token)!r})"
