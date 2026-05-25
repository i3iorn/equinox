"""Environment variable secret manager backend.

Useful for development and testing. Maps secret names to environment variable names.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from equinox.core.secret_managers.base import (
    SecretManager,
    SecretManagerError,
    SecretNotFoundError,
)

logger = logging.getLogger(__name__)


class EnvironmentVariableManager(SecretManager):
    """Simple secret manager backed by environment variables.

    Useful for development and testing. Maps secret names to environment variable names.

    Example:
        secret_name: "db_password" → environment variable: "EQUINOX_SECRET_DB_PASSWORD"
    """

    def __init__(self, prefix: str = "EQUINOX_SECRET_", **kwargs: Any) -> None:
        """Initialize environment variable manager.

        Args:
            prefix: Prefix for environment variable names
            **kwargs: Additional parameters (passed to parent)
        """
        super().__init__(**kwargs)
        self.prefix = prefix

    def configure(self, **kwargs: Any) -> None:
        """Configure environment variable prefix.

        Args:
            prefix: Override the default prefix (optional)
        """
        if "prefix" in kwargs:
            self.prefix = kwargs["prefix"]
        self._configured = True
        logger.info("Environment variable manager configured with prefix: %s", self.prefix)

    def get_secret(self, secret_name: str) -> str:
        """Retrieve a secret from environment variables.

        Args:
            secret_name: Environment variable name (or suffix if prefix is used)

        Returns:
            The environment variable value

        Raises:
            SecretNotFoundError: If the environment variable is not set
        """
        # Check cache first
        cached = self._get_from_cache(secret_name)
        if cached is not None:
            return str(cached)

        env_var_name = f"{self.prefix}{secret_name}".upper()
        value = os.environ.get(env_var_name)

        if value is None:
            raise SecretNotFoundError(f"Environment variable not found: {env_var_name}")

        self._validate_secret_length(value, secret_name)
        self._store_in_cache(secret_name, value)
        return value

    def get_secret_dict(self, secret_name: str) -> dict[str, Any]:
        """Retrieve a secret from environment variables as JSON.

        Args:
            secret_name: Environment variable name suffix

        Returns:
            Parsed JSON dictionary

        Raises:
            SecretNotFoundError: If the environment variable is not set
            SecretManagerError: If the value is not valid JSON
        """
        value = self.get_secret(secret_name)
        try:
            parsed = json.loads(value)
            if not isinstance(parsed, dict):
                raise SecretManagerError(f"Secret '{secret_name}' must decode to a JSON object")
            return parsed
        except json.JSONDecodeError as exc:
            raise SecretManagerError(f"Secret '{secret_name}' is not valid JSON: {exc}") from exc

    def is_available(self) -> bool:
        """Environment variables are always available."""
        return True
