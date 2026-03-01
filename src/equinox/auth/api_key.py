"""API Key authentication"""

from typing import Dict, Any, Literal
from equinox.auth.base import AuthStrategy


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
            ValueError: If location is not 'header' or 'query'
        """
        if location not in ("header", "query"):
            raise ValueError(f"Invalid location '{location}'. Must be 'header' or 'query'")

        self.key = key
        self.value = value
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
        """Create from dictionary"""
        return cls(key=data["key"], value=data["value"], location=data.get("location", "header"))

    def __repr__(self) -> str:
        masked_value = f"{self.value[:4]}..." if len(self.value) > 4 else "***"
        return f"APIKeyAuth(key={self.key}, value={masked_value}, location={self.location})"
