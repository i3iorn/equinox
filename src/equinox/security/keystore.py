"""OS-backed encryption key storage (optional).

This module provides a thin abstraction around storing the 32-byte
encryption key used by Fernet in an OS-native key store when enabled via
the EQUINOX_USE_OS_KEYRING environment flag. If the OS key store isn't
available or the feature is disabled, the fallback is to use the existing
local file-based key in ~/.equinox/.key (maintained for compatibility).
"""

from __future__ import annotations

import base64
import logging
import os

try:
    import keyring  # type: ignore
except Exception:  # pragma: no cover - missing optional dependency
    keyring = None  # type: ignore

from equinox.core.config.flags import is_os_keystore_enabled

logger = logging.getLogger(__name__)

SERVICE = "equinox"
ACCOUNT = "encryption-key"


def _os_keyring_available() -> bool:
    return keyring is not None


def _env_os_keystore_enabled() -> bool:
    return is_os_keystore_enabled()


def get_from_os_store() -> bytes | None:
    """Return the 32-byte key from the OS key store if available."""
    if not _env_os_keystore_enabled() or not _os_keyring_available():
        return None
    try:
        # keyring stores strings; encode/decode as base64 to preserve raw bytes
        b64 = keyring.get_password(SERVICE, ACCOUNT)
        if not b64:
            return None
        return base64.b64decode(b64.encode("ascii"))
    except Exception:
        logger.exception("Failed to retrieve encryption key from OS keyring")
        return None


def set_in_os_store(key: bytes) -> None:
    if not _env_os_keystore_enabled() or not _os_keyring_available():
        return
    try:
        keyring.set_password(SERVICE, ACCOUNT, base64.b64encode(key).decode("ascii"))
    except Exception:
        logger.exception("Failed to store encryption key in OS keyring")


def get_or_create_os_key() -> bytes | None:
    """Return an existing OS-store key or generate and store one.

    Returns:
        The 32-byte raw key if OS keyring is enabled or None if the feature
        is not available/disabled.
    """
    key = get_from_os_store()
    if key is not None:
        return key

    if not _env_os_keystore_enabled() or not _os_keyring_available():
        return None

    # Generate and persist a new 32-byte key.
    key = os.urandom(32)
    set_in_os_store(key)
    return key
