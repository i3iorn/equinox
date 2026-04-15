"""API Key authentication"""

import logging
from typing import Any, Callable, Dict, Literal, Optional
from equinox.auth.base import AuthStrategy, _validate_credential, _interpolate_field
from equinox.core.exceptions import AuthError

logger = logging.getLogger(__name__)

__all__ = ["APIKeyAuth"]

_VALID_LOCATIONS = frozenset({"header", "query"})

# Preview length for masked display
_PREVIEW_LENGTH = 4


class APIKeyAuth(AuthStrategy):
    """API Key authentication strategy.

    Places a static key/value pair either in a request header or as a
    URL query parameter, depending on *location*.

    Example::

        auth = APIKeyAuth(key="X-Api-Key", value="secret", location="header")
        # Adds:  X-Api-Key: secret

        auth = APIKeyAuth(key="api_key", value="secret", location="query")
        # Appends:  ?api_key=secret
    """

    AUTH_TYPE = "api_key"
    DISPLAY_NAME = "API Key"

    def __init__(
        self,
        key: str,
        value: str,
        location: Literal["header", "query"] = "header",
    ):
        """Initialise API key auth.

        Args:
            key:      Key name (e.g. ``'X-API-Key'``, ``'api_key'``).
            value:    API key value.
            location: Where to place the key — ``'header'`` (default) or ``'query'``.

        Raises:
            AuthError: If *location* is not ``'header'`` or ``'query'``, or if
                *key* / *value* is empty, too long, or contains CRLF characters.
        """
        if location not in _VALID_LOCATIONS:
            raise AuthError(
                f"Invalid location {location!r}. Must be one of: "
                + ", ".join(sorted(_VALID_LOCATIONS))
            )
        self.key = _validate_credential(key, "API key name")
        self.value = _validate_credential(value, "API key value")
        self.location = location

    # ── AuthStrategy interface ────────────────────────────────────────────────

    def apply(self, request: Any, headers: Dict[str, str]) -> None:
        """Inject the API key into *headers* or *request.params*."""
        if self.location == "header":
            headers[self.key] = self.value
            logger.debug("APIKeyAuth applied: key=%r in header", self.key)
        else:  # "query" — the only other valid location
            if getattr(request, "params", None) is None:
                request.params = {}
            request.params[self.key] = self.value
            logger.debug("APIKeyAuth applied: key=%r in query params", self.key)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.AUTH_TYPE,
            "key": self.key,
            "value": self.value,
            "location": self.location,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any], **kwargs: Any) -> "APIKeyAuth":
        """Create from a serialised dictionary.

        Raises:
            AuthError: If required keys are missing or values are invalid.
        """
        try:
            return cls(
                key=data["key"],
                value=data["value"],
                location=data.get("location", "header"),
            )
        except KeyError as exc:
            raise AuthError(
                f"Invalid {cls.__name__} data: missing key {exc}"
            ) from exc

    # ── Strategy metadata ─────────────────────────────────────────────────────

    def get_display_summary(self) -> str:
        preview = (
            self.value[:_PREVIEW_LENGTH] + "…"
            if len(self.value) > _PREVIEW_LENGTH
            else "***"
        )
        return f"{self.key} = {preview}  ({self.location})"

    def get_preflight_warning(self) -> Optional[str]:
        if not self.value:
            return "API key value is empty"
        return None

    # ── Dunder helpers ────────────────────────────────────────────────────────

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, APIKeyAuth):
            return NotImplemented
        return self.key == other.key and self.value == other.value and self.location == other.location

    def __hash__(self) -> int:
        return hash((self.key, self.value, self.location))

    def __repr__(self) -> str:
        masked = f"{self.value[:4]}..." if len(self.value) > 4 else "***"
        return f"APIKeyAuth(key={self.key!r}, value={masked!r}, location={self.location!r})"
