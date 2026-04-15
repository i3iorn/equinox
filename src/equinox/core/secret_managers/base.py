"""Base classes and exceptions for secret managers.

This module provides the abstract base class and shared utilities for all
secret manager implementations.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Maximum age for cached secrets (seconds)
_DEFAULT_CACHE_TTL = 300

# Maximum length for a single secret value (prevent accidental storage of entire files)
_MAX_SECRET_LENGTH = 1_000_000


class SecretManagerError(Exception):
    """Base exception for secret manager failures."""

    pass


class SecretNotFoundError(SecretManagerError):
    """Raised when a secret is not found in the manager."""

    pass


class SecretAuthError(SecretManagerError):
    """Raised when authentication with the secret manager fails."""

    pass


class SecretCacheEntry:
    """Container for a cached secret with TTL.

    Attributes:
        value: The secret value (string or dict)
        retrieved_at: Timestamp when the secret was fetched
        ttl_seconds: How long the cache entry is valid for
    """

    def __init__(self, value: Any, ttl_seconds: int = _DEFAULT_CACHE_TTL) -> None:
        self.value = value
        self.retrieved_at = datetime.utcnow()
        self.ttl_seconds = ttl_seconds

    def is_expired(self) -> bool:
        """Check if the cached entry has expired."""
        age = (datetime.utcnow() - self.retrieved_at).total_seconds()
        return age > self.ttl_seconds


class SecretManager(ABC):
    """Abstract base class for secret manager backends.

    Each implementation must provide methods to:
    1. Configure backend-specific settings (connection strings, credentials, etc.)
    2. Retrieve secrets (both string and dictionary formats)
    3. Validate connectivity to the backend
    """

    def __init__(self, enable_cache: bool = True, cache_ttl: int = _DEFAULT_CACHE_TTL) -> None:
        """Initialize the secret manager.

        Args:
            enable_cache: Whether to cache retrieved secrets in memory
            cache_ttl: Time-to-live for cached entries (seconds)
        """
        self.enable_cache = enable_cache
        self.cache_ttl = cache_ttl
        self._cache: Dict[str, SecretCacheEntry] = {}
        self._configured = False

    @abstractmethod
    def configure(self, **kwargs: Any) -> None:
        """Configure backend-specific settings.

        Each implementation defines what configuration parameters are required.
        This method should validate that all required parameters are provided
        and that the backend is accessible.

        Args:
            **kwargs: Backend-specific configuration parameters

        Raises:
            SecretAuthError: If authentication fails
            SecretManagerError: If configuration is invalid
        """
        pass

    @abstractmethod
    def get_secret(self, secret_name: str) -> str:
        """Retrieve a secret as a string value.

        Args:
            secret_name: Identifier for the secret (format depends on backend)

        Returns:
            The secret value as a string

        Raises:
            SecretNotFoundError: If the secret does not exist
            SecretManagerError: If retrieval fails
        """
        pass

    @abstractmethod
    def get_secret_dict(self, secret_name: str) -> Dict[str, Any]:
        """Retrieve a secret as a dictionary (for structured secrets).

        Args:
            secret_name: Identifier for the secret

        Returns:
            The secret value as a dictionary

        Raises:
            SecretNotFoundError: If the secret does not exist
            SecretManagerError: If retrieval fails or secret is not valid JSON
        """
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """Check if the backend is accessible and configured.

        Returns:
            True if the backend is available, False otherwise
        """
        pass

    def clear_cache(self, secret_name: Optional[str] = None) -> None:
        """Clear cached secret(s).

        Args:
            secret_name: Specific secret to clear, or None to clear all
        """
        if secret_name:
            self._cache.pop(secret_name, None)
            logger.debug("Cleared cache for secret: %s", secret_name)
        else:
            self._cache.clear()
            logger.debug("Cleared entire secret cache")

    def _get_from_cache(self, secret_name: str) -> Optional[Any]:
        """Retrieve a secret from cache if available and not expired.

        Args:
            secret_name: Identifier for the secret

        Returns:
            Cached value if found and valid, None otherwise
        """
        if not self.enable_cache:
            return None

        entry = self._cache.get(secret_name)
        if entry and not entry.is_expired():
            logger.debug("Cache hit for secret: %s", secret_name)
            return entry.value

        if entry:
            del self._cache[secret_name]
            logger.debug("Cache expired for secret: %s", secret_name)

        return None

    def _store_in_cache(self, secret_name: str, value: Any) -> None:
        """Store a secret in the cache.

        Args:
            secret_name: Identifier for the secret
            value: The secret value to cache
        """
        if not self.enable_cache:
            return

        self._cache[secret_name] = SecretCacheEntry(value, self.cache_ttl)
        logger.debug("Cached secret: %s", secret_name)

    @staticmethod
    def _validate_secret_length(value: str, secret_name: str) -> None:
        """Validate that a secret doesn't exceed maximum length.

        Args:
            value: The secret value to validate
            secret_name: For logging purposes

        Raises:
            SecretManagerError: If the secret exceeds maximum length
        """
        if len(value) > _MAX_SECRET_LENGTH:
            raise SecretManagerError(
                f"Secret '{secret_name}' exceeds maximum length ({_MAX_SECRET_LENGTH})"
            )

