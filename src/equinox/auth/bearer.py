"""Bearer token authentication"""

from typing import Dict, Any
from equinox.auth.base import AuthStrategy


class BearerAuth(AuthStrategy):
    """Bearer token authentication"""

    def __init__(self, token: str):
        """
        Initialize bearer auth

        Args:
            token: Bearer token
        """
        self.token = token

    def apply(self, request: Any, headers: Dict[str, str]) -> None:
        """Add Authorization header with bearer token"""
        headers["Authorization"] = f"Bearer {self.token}"

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {"type": "bearer", "token": self.token}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BearerAuth":
        """Create from dictionary"""
        return cls(token=data["token"])

    def __repr__(self) -> str:
        masked_token = f"{self.token[:8]}..." if len(self.token) > 8 else "***"
        return f"BearerAuth(token={masked_token})"
