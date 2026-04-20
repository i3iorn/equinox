"""HashiCorp Vault secret manager backend.

Retrieves secrets from Vault via HTTP API.
"""

from __future__ import annotations

import json
import logging
import hashlib
from typing import Any, Dict, Optional
from urllib.parse import urljoin

from equinox.core.secret_managers.base import (
    SecretManager,
    SecretManagerError,
    SecretNotFoundError,
    SecretAuthError,
)

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
        self.url: Optional[str] = None
        self.token: Optional[str] = None
        self.headers: Dict[str, str] = {}

    def configure(self, url: str, token: str, **kwargs: Any) -> None:
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
                "requests is required for Vault. Install with: pip install requests"
            )

        self.url = url.rstrip("/")
        self.token = token
        self.headers = {"X-Vault-Token": token}

        # Test connectivity
        try:
            response = requests.get(
                f"{self.url}/v1/sys/health",
                headers=self.headers,
                verify=kwargs.get("verify_ssl", True),
                timeout=kwargs.get("timeout", 10)
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
            return cached

        try:
            import requests
        except ImportError:
            raise SecretManagerError("requests is required")

        try:
            url = urljoin(f"{self.url}/", f"v1/{secret_name}")
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
            secret_name_fingerprint = hashlib.sha256(secret_name.encode("utf-8")).hexdigest()[:12]
            logger.debug("Retrieved secret from Vault (secret fingerprint: %s)", secret_name_fingerprint)
            return value_str

        except SecretNotFoundError:
            raise
        except Exception as exc:
            raise SecretManagerError(f"Failed to retrieve secret from Vault: {exc}")

    def get_secret_dict(self, secret_name: str) -> Dict[str, Any]:
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
            url = urljoin(f"{self.url}/", f"v1/{secret_name}")
            response = requests.get(url, headers=self.headers, timeout=10)

            if response.status_code == 404:
                raise SecretNotFoundError(f"Secret not found in Vault: {secret_name}")

            response.raise_for_status()
            data = response.json()

            # Extract the actual secret data
            secret_data = data.get("data", {})
            if isinstance(secret_data, dict) and "data" in secret_data:
                secret_data = secret_data["data"]

            return secret_data

        except SecretNotFoundError:
            raise
        except Exception as exc:
            raise SecretManagerError(f"Failed to retrieve secret from Vault: {exc}")

    def is_available(self) -> bool:
        """Check if Vault is configured and reachable."""
        return self._configured and self.url is not None and self.token is not None

