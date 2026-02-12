"""Basic HTTP authentication"""

import base64
from typing import Dict, Any
from equinox.auth.base import AuthStrategy


class BasicAuth(AuthStrategy):
    """Basic HTTP authentication"""

    def __init__(self, username: str, password: str):
        """
        Initialize basic auth

        Args:
            username: Username
            password: Password
        """
        self.username = username
        self.password = password

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
        """Create from dictionary"""
        return cls(username=data["username"], password=data["password"])

    def __repr__(self) -> str:
        return f"BasicAuth(username={self.username})"
