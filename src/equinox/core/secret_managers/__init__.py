"""Secret Manager integration for reading credentials from external secret stores.

Provides a unified interface for retrieving credentials from various secret management
systems (AWS Secrets Manager, HashiCorp Vault, Bitwarden, Azure Key Vault, 1Password, etc.).

This module enables Equinox to fetch secrets from external stores instead of storing
them locally, improving security posture by centralizing secret management.

Supported Backends
------------------
- Environment Variables: Simple development/testing
- AWS Secrets Manager: Production AWS deployments
- HashiCorp Vault: Enterprise multi-cloud deployments
- Bitwarden: Personal/team vaults with CLI
- Azure Key Vault: Planned
- 1Password: Planned

Usage
-----
    from equinox.core.secret_managers import get_secret_manager, SecretNotFoundError

    mgr = get_secret_manager("bitwarden")
    mgr.configure()

    try:
        secret_value = mgr.get_secret("my-database-creds")
        secret_dict = mgr.get_secret_dict("my-credentials")
    except SecretNotFoundError:
        logger.error("Secret not found in manager")
    except SecretManagerError:
        logger.error("Failed to retrieve secret")

Architecture
-----------
- SecretManager (ABC): Base class for all backends
- Concrete implementations: EnvironmentVariableManager, AWSSecretsManagerBackend, VaultManager, BitwardenManager
- get_secret_manager(): Factory function to retrieve manager instances
- Caching layer: Optional in-memory caching with TTL to reduce backend calls
- Error handling: Custom exceptions for different failure modes

Fortress Mentality
-------------------
- No hardcoding of credentials in code or config files
- Credentials fetched on-demand, not stored in memory longer than necessary
- Support for IAM/managed identities where possible (no long-lived credentials)
- Audit logging of all secret access
- Validation of secret format before use
"""

from __future__ import annotations

# Export base classes and exceptions
from equinox.core.secret_managers.base import (
    SecretManager,
    SecretManagerError,
    SecretNotFoundError,
    SecretAuthError,
    SecretCacheEntry,
)

# Export concrete implementations
from equinox.core.secret_managers.env import EnvironmentVariableManager
from equinox.core.secret_managers.aws import AWSSecretsManagerBackend
from equinox.core.secret_managers.vault import VaultManager
from equinox.core.secret_managers.bitwarden import BitwardenManager

# Export registry functions
from equinox.core.secret_managers.registry import (
    get_secret_manager,
    list_available_managers,
    register_manager,
)
from equinox.core.secret_managers.connection import (
    SecretManagerConnectionResult,
    test_secret_manager_connection,
)
from equinox.core.secret_managers.profiles import SecretManagerProfile

__all__ = [
    # Base classes and exceptions
    "SecretManager",
    "SecretManagerError",
    "SecretNotFoundError",
    "SecretAuthError",
    "SecretCacheEntry",
    # Implementations
    "EnvironmentVariableManager",
    "AWSSecretsManagerBackend",
    "VaultManager",
    "BitwardenManager",
    # Factory
    "get_secret_manager",
    "list_available_managers",
    "register_manager",
    "SecretManagerProfile",
    "SecretManagerConnectionResult",
    "test_secret_manager_connection",
]

