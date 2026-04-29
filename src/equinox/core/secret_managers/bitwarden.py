"""Bitwarden secret manager backend.

Retrieves secrets from Bitwarden via the Bitwarden CLI or API.
Supports both personal vaults and organization vaults.
"""

from __future__ import annotations

import json
import logging
import subprocess
from typing import Any, Dict, Optional

from equinox.core.secret_managers.base import (
    SecretManager,
    SecretManagerError,
    SecretNotFoundError,
    SecretAuthError,
)
from equinox.core.security import mask_secret

logger = logging.getLogger(__name__)


class BitwardenManager(SecretManager):
    """Bitwarden secret manager backend.

    Retrieves secrets from Bitwarden using the official CLI (bw command).
    Supports retrieval by item ID, name, or organization vault.

    Requirements:
        - Bitwarden CLI installed (https://bitwarden.com/help/cli/)
        - User logged in: `bw login user@example.com password`
        - Session unlocked: `bw unlock password`

    Examples:
        # By item ID
        mgr = BitwardenManager()
        mgr.configure()
        secret = mgr.get_secret("a1b2c3d4-e5f6-g7h8-i9j0-k1l2m3n4o5p6")

        # By item name
        secret = mgr.get_secret_dict("database-credentials")

        # From organization vault
        mgr.configure(organization_id="org123")
        secret = mgr.get_secret("secret-name")
    """

    def __init__(self, **kwargs: Any) -> None:
        """Initialize Bitwarden manager."""
        super().__init__(**kwargs)
        self.organization_id: Optional[str] = None
        self.session_token: Optional[str] = None

    def configure(self, organization_id: Optional[str] = None, **kwargs: Any) -> None:
        """Configure Bitwarden connection.

        Args:
            organization_id: Optional organization ID to scope searches
            **kwargs: Additional options (reserved for future use)

        Raises:
            SecretAuthError: If Bitwarden CLI is not available or not logged in
            SecretManagerError: If configuration is invalid
        """
        # Check if bw CLI is available
        try:
            result = subprocess.run(
                ["bw", "--version"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode != 0:
                raise SecretManagerError("Bitwarden CLI not found or not executable")
        except FileNotFoundError:
            raise SecretManagerError(
                "Bitwarden CLI not found. Install from: https://bitwarden.com/help/cli/"
            )
        except Exception as exc:
            raise SecretManagerError(f"Failed to check Bitwarden CLI: {exc}")

        # Test that we can access Bitwarden (this will fail if not logged in)
        try:
            result = subprocess.run(
                ["bw", "status"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode != 0:
                raise SecretAuthError(
                    "Not logged in to Bitwarden. Run: bw login your-email@example.com"
                )

            status = json.loads(result.stdout)
            if status.get("status") == "locked":
                raise SecretAuthError(
                    "Bitwarden vault is locked. Run: bw unlock your-password"
                )
        except json.JSONDecodeError:
            raise SecretManagerError("Invalid Bitwarden status response")
        except Exception as exc:
            if isinstance(exc, SecretAuthError):
                raise
            raise SecretManagerError(f"Failed to connect to Bitwarden: {exc}")

        self.organization_id = organization_id
        self._configured = True
        logger.info(
            "Bitwarden configured%s",
            f" (org: {organization_id})" if organization_id else ""
        )

    def get_secret(self, secret_name: str) -> str:
        """Retrieve a secret from Bitwarden.

        Can retrieve by:
        - Item ID (UUID format)
        - Item name (searches vault)

        Args:
            secret_name: Item ID (UUID) or item name

        Returns:
            The secret value (notes field or password field)

        Raises:
            SecretNotFoundError: If the item does not exist
            SecretManagerError: If retrieval fails
        """
        if not self._configured:
            raise SecretManagerError("Bitwarden not configured")

        # Check cache
        cached = self._get_from_cache(secret_name)
        if cached is not None:
            return cached

        secret_ref = mask_secret(secret_name, keep=4)

        try:
            item = self._get_item(secret_name)

            # Extract secret value (prioritize notes, fall back to password)
            value = item.get("notes") or item.get("password")

            if not value:
                # Try to extract from login object
                if "login" in item and "password" in item["login"]:
                    value = item["login"]["password"]

            if not value:
                raise SecretManagerError(f"No secret value found in item: {secret_ref}")

            value_str = str(value)
            self._validate_secret_length(value_str, secret_name)
            self._store_in_cache(secret_name, value_str)
            logger.debug("Retrieved secret from Bitwarden: %s", secret_ref)
            return value_str

        except SecretNotFoundError:
            raise
        except Exception as exc:
            raise SecretManagerError(f"Failed to retrieve secret from Bitwarden: {exc}")

    def get_secret_dict(self, secret_name: str) -> Dict[str, Any]:
        """Retrieve a secret from Bitwarden as a dictionary.

        Useful for credentials with multiple fields (username, password, etc.).

        Args:
            secret_name: Item ID (UUID) or item name

        Returns:
            Dictionary containing item fields

        Raises:
            SecretNotFoundError: If the item does not exist
            SecretManagerError: If retrieval fails
        """
        if not self._configured:
            raise SecretManagerError("Bitwarden not configured")

        # Check cache
        cache_key = f"{secret_name}:dict"
        cached = self._get_from_cache(cache_key)
        if cached is not None:
            return cached

        secret_ref = mask_secret(secret_name, keep=4)

        try:
            item = self._get_item(secret_name)

            # Build dictionary from item fields
            result = {}

            # Add login credentials if present
            if "login" in item:
                login = item["login"]
                if login.get("username"):
                    result["username"] = login["username"]
                if login.get("password"):
                    result["password"] = login["password"]
                if login.get("uri"):
                    result["uri"] = login["uri"]

            # Add notes
            if item.get("notes"):
                result["notes"] = item["notes"]

            # Add custom fields
            if "fields" in item and item["fields"]:
                for field in item["fields"]:
                    field_name = field.get("name", "")
                    field_value = field.get("value", "")
                    if field_name:
                        result[field_name] = field_value

            # Add name and ID for reference
            result["name"] = item.get("name", "")
            result["id"] = item.get("id", "")

            self._store_in_cache(cache_key, result)
            logger.debug("Retrieved secret dict from Bitwarden: %s", secret_ref)
            return result

        except SecretNotFoundError:
            raise
        except Exception as exc:
            raise SecretManagerError(f"Failed to retrieve secret from Bitwarden: {exc}")

    def is_available(self) -> bool:
        """Check if Bitwarden is configured and accessible."""
        if not self._configured:
            return False

        try:
            result = subprocess.run(
                ["bw", "status"],
                capture_output=True,
                text=True,
                timeout=5
            )
            return result.returncode == 0
        except Exception:
            return False

    def _get_item(self, secret_name: str) -> Dict[str, Any]:
        """Internal method to retrieve an item from Bitwarden.

        Args:
            secret_name: Item ID or name

        Returns:
            Item dictionary

        Raises:
            SecretNotFoundError: If item not found
            SecretManagerError: If retrieval fails
        """
        secret_ref = mask_secret(secret_name, keep=4)
        try:
            # Try to get by ID first (assumes UUID format)
            if self._is_uuid(secret_name):
                result = subprocess.run(
                    ["bw", "get", "item", secret_name],
                    capture_output=True,
                    text=True,
                    timeout=10
                )
            else:
                # Search by name
                search_result = subprocess.run(
                    ["bw", "list", "items", "--search", secret_name],
                    capture_output=True,
                    text=True,
                    timeout=10
                )

                if search_result.returncode != 0:
                    raise SecretNotFoundError(f"Item not found: {secret_ref}")

                items = json.loads(search_result.stdout)
                if not items:
                    raise SecretNotFoundError(f"Item not found: {secret_ref}")

                # Use the first matching item
                item_id = items[0]["id"]
                result = subprocess.run(
                    ["bw", "get", "item", item_id],
                    capture_output=True,
                    text=True,
                    timeout=10
                )

            if result.returncode != 0:
                stderr = result.stderr.lower()
                if "not found" in stderr or "invalid" in stderr:
                    raise SecretNotFoundError(f"Item not found: {secret_ref}")
                raise SecretManagerError(f"Failed to retrieve item: {result.stderr}")

            return json.loads(result.stdout)

        except json.JSONDecodeError as exc:
            raise SecretManagerError(f"Invalid Bitwarden response: {exc}")
        except subprocess.TimeoutExpired:
            raise SecretManagerError("Bitwarden CLI request timed out")
        except Exception as exc:
            if isinstance(exc, (SecretNotFoundError, SecretManagerError)):
                raise
            raise SecretManagerError(f"Failed to retrieve item from Bitwarden: {exc}")

    @staticmethod
    def _is_uuid(value: str) -> bool:
        """Check if a value looks like a UUID.

        Args:
            value: String to check

        Returns:
            True if value matches UUID format
        """
        import re
        uuid_pattern = re.compile(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
            re.IGNORECASE
        )
        return bool(uuid_pattern.match(value))

