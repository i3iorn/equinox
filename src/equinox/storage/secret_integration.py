"""Integration between secret managers and Equinox's saved credentials system.

Allows saved credentials to pull their values from external secret managers
instead of storing them in the local database, improving security.

Credentials can be configured with a "secret_source" field that specifies:
- Manager type (e.g., "aws", "vault")
- Secret identifier (e.g., path, name, or ARN in the secret manager)
- Optional parameters (e.g., JSON key paths, transformations)

Architecture
-----------
When loading a credential with a secret_source:
1. Extract the manager type and secret identifier
2. Instantiate the appropriate secret manager backend
3. Retrieve the secret value from the external store
4. Cache the result (if caching enabled)
5. Return the credential with values filled in from the secret

Database Schema
---------------
The saved_credentials table gains two optional columns:
- secret_source_type: Manager type identifier (e.g., "aws", "vault")
- secret_source_config: JSON with manager-specific config (path, key paths, etc.)

Example configurations:
    {
        "type": "aws",
        "secret_name": "prod/db-credentials",
        "json_keys": ["username", "password"]
    }

    {
        "type": "vault",
        "path": "secret/data/app/api-key",
        "key": "api_key"
    }

Usage Example
-------------
    from equinox.storage.saved_credentials import SavedCredentialsManager
    from equinox.storage.secret_integration import load_credential_with_secrets

    mgr = SavedCredentialsManager(db)
    cred_row = mgr.get(credential_id)

    # Retrieve secret values from external managers
    cred_with_secrets = load_credential_with_secrets(cred_row)

    # Or when instantiating auth strategies:
    auth = create_auth_from_credential_with_secrets(cred_row)

Security Considerations
-----------------------
- Secrets are never stored in the database; only references are stored
- Secrets are cached in-memory with configurable TTL
- All secret retrieval is logged for audit purposes
- Failed secret retrieval results in clear error messages
- Credentials can fall back to local stored values if external store unavailable
"""
from __future__ import annotations
from equinox.security.secret_integration import *  # type: ignore

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from equinox.core.exceptions import StorageError, SecurityError, AuthError
from equinox.security import mask_secret, redact_body
from equinox.core.secret_managers import (
    get_secret_manager,
    SecretManager,
    SecretNotFoundError,
    SecretManagerError,
)

logger = logging.getLogger(__name__)


class CredentialSecretResolver:
    """Resolves credential values from external secret managers.

    Handles the logic of mapping saved credential records to external secrets
    and merging external secret values with locally-stored credentials.
    """

    def __init__(self, cache_ttl: int = 300) -> None:
        """Initialize resolver.

        Args:
            cache_ttl: Cache time-to-live for secret manager results (seconds)
        """
        self.cache_ttl = cache_ttl
        self._managers: Dict[str, SecretManager] = {}

    def get_manager(self, manager_type: str) -> SecretManager:
        """Get or create a secret manager instance.

        Args:
            manager_type: Type identifier (e.g., "aws", "vault")

        Returns:
            Configured secret manager instance

        Raises:
            StorageError: If manager type is unknown or not available
        """
        if manager_type in self._managers:
            return self._managers[manager_type]

        try:
            mgr = get_secret_manager(manager_type, cache_ttl=self.cache_ttl)
            self._managers[manager_type] = mgr
            return mgr
        except Exception as exc:
            raise StorageError(f"Failed to initialize secret manager '{manager_type}': {exc}")

    def resolve_secret_value(
        self, manager_type: str, config: Dict[str, Any]
    ) -> str:
        """Retrieve a secret value from an external manager.

        Args:
            manager_type: Type of secret manager (e.g., "aws", "vault")
            config: Manager-specific configuration dict with:
                - For AWS: "secret_name" key with the AWS secret name/ARN
                - For Vault: "path" key with the secret path
                - May include "key" to extract a specific field from JSON secrets

        Returns:
            The resolved secret value as a string

        Raises:
            SecretNotFoundError: If the secret does not exist
            StorageError: If resolution fails
        """
        manager = self.get_manager(manager_type)

        # Validate that required config is present
        if not config:
            raise StorageError("Secret manager config cannot be empty")

        # Determine the secret identifier based on manager type
        secret_identifier = config.get("secret_name") or config.get("path")
        if not secret_identifier:
            raise StorageError("Secret config must include 'secret_name' or 'path'")

        secret_ref = mask_secret(str(secret_identifier), keep=4)

        try:
            # For JSON secrets, optionally extract a specific key
            if config.get("json_keys") or config.get("key"):
                secret_dict = manager.get_secret_dict(secret_identifier)

                # If a single key is specified, return its value
                if config.get("key"):
                    key = config["key"]
                    if key not in secret_dict:
                        raise StorageError(
                            f"Key '{key}' not found in secret '{secret_ref}'"
                        )
                    return str(secret_dict[key])

                # If json_keys is specified, validate those keys exist
                # (used for validation, actual value retrieval happens at auth level)
                json_keys = config.get("json_keys", [])
                for key in json_keys:
                    if key not in secret_dict:
                        raise StorageError(
                            f"Required key '{key}' not found in secret '{secret_ref}'"
                        )

                # Return the entire dict as JSON string if multiple keys needed
                import json
                return json.dumps(secret_dict)
            else:
                # Plain string secret
                return manager.get_secret(secret_identifier)

        except SecretNotFoundError:
            logger.warning("Secret not found in %s: %s", manager_type, secret_ref)
            raise
        except Exception as exc:
            safe_error = redact_body(str(exc), max_length=200) or "secret resolution failed"
            logger.error(
                "Failed to resolve secret from %s: %s",
                manager_type,
                safe_error,
            )
            raise StorageError(f"Failed to retrieve secret: {safe_error}")

    def hydrate_credential(
        self, credential_row: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Merge external secret values into a credential row.

        If the credential has a secret_source_type and secret_source_config,
        retrieves the secret from the external manager and merges it into
        the credential's config dict.

        Args:
            credential_row: Row from saved_credentials table

        Returns:
            Updated credential row with secrets filled in

        Raises:
            StorageError: If secret resolution fails
        """
        source_type = credential_row.get("secret_source_type")
        source_config = credential_row.get("secret_source_config")

        # If no secret source configured, return as-is
        if not source_type or not source_config:
            return credential_row

        try:
            # Resolve the secret value
            secret_value = self.resolve_secret_value(source_type, source_config)

            # Parse the secret value based on whether it's JSON or plain string
            if isinstance(source_config, dict) and source_config.get("json_keys"):
                import json
                secret_dict = json.loads(secret_value)
            else:
                secret_dict = {"value": secret_value}

            # Merge secrets into the credential config
            updated_row = credential_row.copy()
            config = credential_row.get("config", {}).copy()

            # Update config with secret values
            config.update(secret_dict)
            updated_row["config"] = config

            logger.debug(
                "Hydrated credential %d with secrets from %s",
                credential_row.get("id"),
                source_type,
            )
            return updated_row

        except Exception as exc:
            logger.error(
                "Failed to hydrate credential %d: %s",
                credential_row.get("id"),
                exc,
            )
            raise


def load_credential_with_secrets(
    credential_row: Dict[str, Any], resolver: Optional[CredentialSecretResolver] = None
) -> Dict[str, Any]:
    """Load a credential and resolve any external secrets.

    Convenience function that creates a resolver and hydrates the credential
    in a single call.

    Args:
        credential_row: Row from saved_credentials table
        resolver: Optional CredentialSecretResolver instance (creates one if None)

    Returns:
        Credential row with external secrets resolved

    Raises:
        StorageError: If secret resolution fails
    """
    if resolver is None:
        resolver = CredentialSecretResolver()

    return resolver.hydrate_credential(credential_row)


def create_auth_from_credential_with_secrets(
    credential_row: Dict[str, Any],
    resolver: Optional[CredentialSecretResolver] = None,
) -> Any:
    """Create an auth strategy from a credential, resolving external secrets.

    Args:
        credential_row: Row from saved_credentials table
        resolver: Optional CredentialSecretResolver instance

    Returns:
        Configured auth strategy object

    Raises:
        StorageError: If secret resolution fails
    """
    from equinox.auth.factory import auth_from_dict

    # Hydrate with secrets
    hydrated = load_credential_with_secrets(credential_row, resolver)

    # Create auth strategy from hydrated config
    auth_type = hydrated.get("auth_type")
    config = hydrated.get("config", {})

    if not auth_type:
        raise StorageError("Credential missing auth_type")

    return auth_from_dict(auth_type, config)


# Global resolver instance (lazy-initialized)
_global_resolver: Optional[CredentialSecretResolver] = None


def get_global_resolver() -> CredentialSecretResolver:
    """Get the global CredentialSecretResolver instance.

    Creates one on first call, reusing it thereafter.

    Returns:
        Global CredentialSecretResolver instance
    """
    global _global_resolver
    if _global_resolver is None:
        _global_resolver = CredentialSecretResolver()
    return _global_resolver


def clear_global_cache() -> None:
    """Clear all caches in the global resolver."""
    resolver = get_global_resolver()
    for manager in resolver._managers.values():
        manager.clear_cache()
    logger.debug("Cleared global secret manager caches")

