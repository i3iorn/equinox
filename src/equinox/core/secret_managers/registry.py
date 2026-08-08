"""Secret manager registry and factory functions.

Provides a unified interface for getting secret manager instances.
"""

from __future__ import annotations

import logging
from typing import cast

from collections.abc import Callable

from equinox.core.secret_managers.base import SecretManager, SecretManagerError

logger = logging.getLogger(__name__)

# Default cache TTL
_DEFAULT_CACHE_TTL = 300


def _get_env_manager() -> type[SecretManager]:
    """Lazy loader for EnvironmentVariableManager."""
    from equinox.core.secret_managers.env import EnvironmentVariableManager

    return cast(type[SecretManager], EnvironmentVariableManager)


def _get_aws_manager() -> type[SecretManager]:
    """Lazy loader for AWSSecretsManagerBackend."""
    from equinox.core.secret_managers.aws import AWSSecretsManagerBackend

    return cast(type[SecretManager], AWSSecretsManagerBackend)


def _get_vault_manager() -> type[SecretManager]:
    """Lazy loader for VaultManager."""
    from equinox.core.secret_managers.vault import VaultManager

    return cast(type[SecretManager], VaultManager)


def _get_bitwarden_manager() -> type[SecretManager]:
    """Lazy loader for BitwardenManager."""
    from equinox.core.secret_managers.bitwarden import BitwardenManager

    return cast(type[SecretManager], BitwardenManager)


# Registry of available secret manager backends
# Maps type identifiers to lazy-loading functions
_SECRET_MANAGERS: dict[str, Callable[[], type[SecretManager]]] = {
    "env": _get_env_manager,
    "environment": _get_env_manager,
    "aws_secrets_manager": _get_aws_manager,
    "aws": _get_aws_manager,
    "vault": _get_vault_manager,
    "hashicorp_vault": _get_vault_manager,
    "bitwarden": _get_bitwarden_manager,
    "bw": _get_bitwarden_manager,
}

# Singleton instances (lazy-loaded)
_instances: dict[str, SecretManager] = {}


def get_secret_manager(
    manager_type: str,
    enable_cache: bool = True,
    cache_ttl: int = _DEFAULT_CACHE_TTL,
) -> SecretManager:
    """Get or create a secret manager instance.

    Args:
        manager_type: Type of manager ("env", "aws", "vault", "bitwarden", etc.)
        enable_cache: Whether to enable result caching
        cache_ttl: Cache time-to-live in seconds

    Returns:
        Configured secret manager instance

    Raises:
        SecretManagerError: If the manager type is unknown

    Examples:
        # Environment variables
        mgr = get_secret_manager("env")
        mgr.configure()

        # AWS Secrets Manager
        mgr = get_secret_manager("aws")
        mgr.configure(region_name="us-east-1")

        # Vault
        mgr = get_secret_manager("vault")
        mgr.configure(url="https://vault.example.com:8200", token="hvs...")

        # Bitwarden
        mgr = get_secret_manager("bitwarden")
        mgr.configure()
    """
    manager_type_lower = manager_type.lower()

    if manager_type_lower not in _SECRET_MANAGERS:
        available = ", ".join(sorted(_SECRET_MANAGERS.keys()))
        raise SecretManagerError(
            f"Unknown secret manager type: {manager_type}. Available: {available}",
        )

    # Check if we already have an instance with the same configuration
    cache_key = f"{manager_type_lower}:{enable_cache}:{cache_ttl}"
    if cache_key in _instances:
        return _instances[cache_key]

    # Create new instance using lazy-loaded class
    loader = _SECRET_MANAGERS[manager_type_lower]
    manager_class = loader()
    instance = manager_class(enable_cache=enable_cache, cache_ttl=cache_ttl)
    _instances[cache_key] = instance

    logger.debug("Created secret manager instance: %s", manager_type_lower)
    return instance


def list_available_managers() -> list[str]:
    """Get a list of available secret manager types.

    Returns:
        List of manager type identifiers, sorted alphabetically
    """
    return sorted(set(_SECRET_MANAGERS.keys()))


def register_manager(
    manager_type: str,
    loader_func: Callable[[], type[SecretManager]],
) -> None:
    """Register a custom secret manager implementation.

    Args:
        manager_type: Type identifier for the manager
        loader_func: Callable that returns the manager class

    Example:
        from equinox.core.secret_managers.registry import register_manager

        class MyCustomManager(SecretManager):
            # ... implementation ...
            pass

        register_manager("custom", lambda: MyCustomManager)
        mgr = get_secret_manager("custom")
    """
    _SECRET_MANAGERS[manager_type.lower()] = loader_func
    logger.info("Registered custom secret manager: %s", manager_type.lower())
