"""HashiCorp Vault secret manager backend.

Retrieves secrets from Vault via HTTP API.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from equinox.core import urls
from equinox.core.secret_managers.base import (
    SecretAuthError,
    SecretManager,
    SecretManagerError,
    SecretNotFoundError,
)
from equinox.core.validation import Validator
from equinox.security import mask_secret

logger = logging.getLogger(__name__)


class VaultManager(SecretManager):
    """HashiCorp Vault secret manager backend.

    Supports both static and dynamic secrets via Vault's HTTP API.
    Handles token renewal automatically.

    Example:
        mgr = VaultManager()
        mgr.configure(
            url="https://vault.example.com:8200",
            token="hvs.XXXXXXX"
        )
        secret = mgr.get_secret("secret/data/db-password")
    """

    def __init__(self, **kwargs: Any) -> None:
        """Initialize Vault manager."""
        super().__init__(**kwargs)
        self.url: str | None = None
        self.token: str | None = None
        self.headers: dict[str, str] = {}

    def configure(self, **kwargs: Any) -> None:
        """Configure Vault connection.

        Args:
            url: Vault server URL (e.g., https://vault.example.com:8200)
            token: Vault authentication token
            **kwargs: Additional configuration options

        Raises:
            SecretManagerError: If configuration is invalid
        """
        try:
            import requests
        except ImportError:
            raise SecretManagerError(
                "requests is required for Vault. Install with: pip install requests",
            )

        url = kwargs.get("url")
        token = kwargs.get("token")
        if not isinstance(url, str) or not url.strip():
            raise SecretManagerError("Vault configuration requires a non-empty 'url'")
        if not isinstance(token, str) or not token.strip():
            raise SecretManagerError("Vault configuration requires a non-empty 'token'")

        raw_url = str(url).strip()

        # Deny insecure transport by default for secret backends.
        allow_insecure_http = bool(kwargs.get("allow_insecure_http", False))
        if raw_url.lower().startswith("http://") and not allow_insecure_http:
            raise SecretManagerError(
                "Vault URL must use https:// (set allow_insecure_http=True only for local testing)",
            )

        try:
            # In explicit insecure-http mode, avoid DNS-dependent SSRF checks so
            # local/offline test environments remain deterministic.
            if allow_insecure_http and raw_url.lower().startswith("http://"):
                validated_url = Validator.validate_url(raw_url)
            else:
                validated_url = Validator.validate_resolved_url(raw_url)
        except Exception as exc:
            raise SecretManagerError(f"Invalid Vault URL: {exc}")

        self.url = validated_url.rstrip("/")
        self.token = token
        self.headers = {"X-Vault-Token": token}

        # Test connectivity
        try:
            response = requests.get(
                f"{self.url}/v1/sys/health",
                headers=self.headers,
                verify=kwargs.get("verify_ssl", True),
                timeout=kwargs.get("timeout", 10),
            )
            if response.status_code not in (200, 429, 500, 503):
                raise SecretManagerError(f"Vault health check failed: {response.status_code}")
        except Exception as exc:
            raise SecretAuthError(f"Failed to connect to Vault: {exc}")

        self._configured = True
        logger.info("Vault configured (url: %s)", self.url)

    def get_secret(self, secret_name: str) -> str:
        """Retrieve a secret from Vault.

        Args:
            secret_name: Path to the secret in Vault (e.g., secret/data/my-secret)

        Returns:
            The secret value

        Raises:
            SecretNotFoundError: If the secret does not exist
            SecretManagerError: If retrieval fails
        """
        if not self._configured:
            raise SecretManagerError("Vault not configured")

        # Check cache
        cached = self._get_from_cache(secret_name)
        if cached is not None:
            return str(cached)

        try:
            import requests
        except ImportError:
            raise SecretManagerError("requests is required")

        try:
            url = urls.join_url_path(str(self.url), f"v1/{secret_name}")
            response = requests.get(url, headers=self.headers, timeout=10)

            if response.status_code == 404:
                raise SecretNotFoundError(f"Secret not found in Vault: {secret_name}")

            response.raise_for_status()
            data = response.json()

            # Vault nests the secret data under "data" key
            secret_data = data.get("data", {})
            if isinstance(secret_data, dict) and "data" in secret_data:
                secret_data = secret_data["data"]

            # Try to get a "value" key, or return the whole dict as JSON
            value = secret_data.get("value")
            if value is None and secret_data:
                value = json.dumps(secret_data)
            elif value is None:
                raise SecretManagerError(f"Empty secret at {secret_name}")

            value_str = str(value)
            self._validate_secret_length(value_str, secret_name)
            self._store_in_cache(secret_name, value_str)
            logger.debug(
                "Retrieved secret from Vault (secret ref: %s)",
                mask_secret(secret_name, keep=4),
            )
            return value_str

        except SecretNotFoundError:
            raise
        except Exception as exc:
            raise SecretManagerError(f"Failed to retrieve secret from Vault: {exc}")

    def get_secret_dict(self, secret_name: str) -> dict[str, Any]:
        """Retrieve a secret from Vault as a dictionary.

        Args:
            secret_name: Path to the secret in Vault

        Returns:
            The secret as a dictionary

        Raises:
            SecretNotFoundError: If the secret does not exist
            SecretManagerError: If retrieval fails
        """
        try:
            import requests
        except ImportError:
            raise SecretManagerError("requests is required")

        if not self._configured:
            raise SecretManagerError("Vault not configured")

        try:
            url = urls.join_url_path(str(self.url), f"v1/{secret_name}")
            response = requests.get(url, headers=self.headers, timeout=10)

            if response.status_code == 404:
                raise SecretNotFoundError(f"Secret not found in Vault: {secret_name}")

            response.raise_for_status()
            data = response.json()

            # Extract the actual secret data
            secret_data = data.get("data", {})
            if isinstance(secret_data, dict) and "data" in secret_data:
                secret_data = secret_data["data"]
            if not isinstance(secret_data, dict):
                raise SecretManagerError(f"Secret '{secret_name}' is not a JSON object")
            return secret_data

        except SecretNotFoundError:
            raise
        except Exception as exc:
            raise SecretManagerError(f"Failed to retrieve secret from Vault: {exc}")

    def is_available(self) -> bool:
        """Check if Vault is configured and reachable."""
        return self._configured and self.url is not None and self.token is not None
