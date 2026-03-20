"""API Key authentication"""

from typing import Dict, Any, Literal
from equinox.auth.base import AuthStrategy, _validate_credential
from equinox.core.exceptions import AuthError


class APIKeyAuth(AuthStrategy):
    """API Key authentication (header or query parameter)"""

    def __init__(
        self,
        key: str,
        value: str,
        location: Literal["header", "query"] = "header",
    ):
        """
        Initialize API key auth

        Args:
            key: Key name (e.g., 'X-API-Key', 'api_key')
            value: API key value
            location: Where to place the key ('header' or 'query')

        Raises:
            AuthError: If location is not 'header' or 'query', or if key/value
                is empty, too long, or contains CRLF characters.
        """
        if location not in ("header", "query"):
            raise AuthError(f"Invalid location '{location}'. Must be 'header' or 'query'")

        self.key = _validate_credential(key, "API key name")
        self.value = _validate_credential(value, "API key value")
        self.location = location

    def apply(self, request: Any, headers: Dict[str, str]) -> None:
        """Add API key to headers or query params"""
        if self.location == "header":
            headers[self.key] = self.value
        elif self.location == "query":
            if not hasattr(request, "params"):
                request.params = {}
            request.params[self.key] = self.value

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            "type": "api_key",
            "key": self.key,
            "value": self.value,
            "location": self.location,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "APIKeyAuth":
        """Create from dictionary.

        Raises:
            AuthError: If required keys are missing or values are invalid.
        """
        try:
            return cls(key=data["key"], value=data["value"], location=data.get("location", "header"))
        except KeyError as exc:
            raise AuthError(f"Invalid API key auth data: missing key {exc}") from exc

    def __repr__(self) -> str:
        masked_value = f"{self.value[:4]}..." if len(self.value) > 4 else "***"
        return f"APIKeyAuth(key={self.key}, value={masked_value}, location={self.location})"
