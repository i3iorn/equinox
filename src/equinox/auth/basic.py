"""Basic HTTP authentication"""

import base64
from typing import Dict, Any
from equinox.auth.base import AuthStrategy, _validate_credential
from equinox.core.exceptions import AuthError


class BasicAuth(AuthStrategy):
    """Basic HTTP authentication"""

    def __init__(self, username: str, password: str):
        """
        Initialize basic auth

        Args:
            username: Username
            password: Password

        Raises:
            AuthError: If username or password is empty, too long, or
                contains CRLF characters.
        """
        self.username = _validate_credential(username, "Username")
        self.password = _validate_credential(password, "Password")

    def apply(self, request: Any, headers: Dict[str, str]) -> None:
        """Add Authorization header with basic auth credentials"""
        credentials = f"{self.username}:{self.password}"
        encoded = base64.b64encode(credentials.encode()).decode()
        headers["Authorization"] = f"Basic {encoded}"

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {"type": "basic", "username": self.username, "password": self.password}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BasicAuth":
        """Create from dictionary.

        Raises:
            AuthError: If required keys are missing or values are invalid.
        """
        try:
            return cls(username=data["username"], password=data["password"])
        except KeyError as exc:
            raise AuthError(f"Invalid basic auth data: missing key {exc}") from exc

    def __repr__(self) -> str:
        masked = f"{self.username[:2]}****" if len(self.username) > 2 else "****"
        return f"BasicAuth(username={masked})"
