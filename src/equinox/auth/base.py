"""Base authentication strategy"""

from abc import ABC, abstractmethod
from typing import Dict, Any


class AuthStrategy(ABC):
    """Base class for authentication strategies"""

    @abstractmethod
    def apply(self, request: Any, headers: Dict[str, str]) -> None:
        """
        Apply authentication to request headers

        Args:
            request: Request object
            headers: Headers dictionary to modify
        """
        pass

    @abstractmethod
    def to_dict(self) -> Dict[str, Any]:
        """Convert auth strategy to dictionary for storage"""
        pass

    @classmethod
    @abstractmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AuthStrategy":
        """Create auth strategy from dictionary"""
        pass
