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
from typing import Any

from equinox.core.config.flags import is_os_keystore_enabled

keyring: Any | None
try:
    import keyring as _keyring

    keyring = _keyring
except Exception:  # pragma: no cover - missing optional dependency
    keyring = None

logger = logging.getLogger(__name__)

SERVICE = "equinox"
ACCOUNT = "encryption-key"


def _os_keyring_available() -> bool:
    return keyring is not None


def _env_os_keystore_enabled() -> bool:
    return is_os_keystore_enabled()


class KeystoreUnavailableError(RuntimeError):
    """The OS key store is enabled but could not be read.

    Deliberately distinct from "no key stored": the store holds a key (or may
    hold one) but the lookup itself failed. Callers must not generate a
    replacement, because that would orphan everything encrypted under the
    existing key.
    """


def _keyring_has_no_backend(exc: Exception) -> bool:
    """True when *exc* means "no usable backend", not "backend failed".

    A machine without a configured backend is "unavailable" and may fall back
    to the file key; a backend that exists but errors (locked keychain) may
    not, because replacing the key there silently loses data.
    """
    errors = getattr(keyring, "errors", None)
    if errors is None:
        return False
    for name in ("NoKeyringError", "InitError"):
        exc_cls = getattr(errors, name, None)
        if exc_cls is not None and isinstance(exc, exc_cls):
            return True
    return False


def get_from_os_store() -> bytes | None:
    """Return the 32-byte key from the OS key store if available.

    Raises:
        KeystoreUnavailableError: the store is enabled and has a backend, but
            the lookup failed. Never treat this as "no key stored".
    """
    if not _env_os_keystore_enabled() or not _os_keyring_available():
        return None
    kr = keyring
    if kr is None:
        return None
    try:
        # keyring stores strings; encode/decode as base64 to preserve raw bytes
        b64 = kr.get_password(SERVICE, ACCOUNT)
    except Exception as exc:
        if _keyring_has_no_backend(exc):
            logger.warning(
                "OS keyring has no usable backend; falling back to the file key",
                exc_info=True,
            )
            return None
        logger.exception("Failed to retrieve encryption key from OS keyring")
        raise KeystoreUnavailableError(
            "OS keyring is enabled but could not be read. Refusing to replace "
            "the stored key, because doing so would make previously encrypted "
            "values unreadable. Unlock the keychain, or set "
            "EQUINOX_USE_OS_KEYRING=0 to use the file key.",
        ) from exc

    if not b64:
        return None
    try:
        return base64.b64decode(b64.encode("ascii"))
    except Exception as exc:
        logger.exception("OS keyring value is not valid base64")
        raise KeystoreUnavailableError(
            "The key stored in the OS keyring is corrupt (not valid base64).",
        ) from exc


def set_in_os_store(key: bytes) -> None:
    if not _env_os_keystore_enabled() or not _os_keyring_available():
        return
    kr = keyring
    if kr is None:
        return
    try:
        kr.set_password(SERVICE, ACCOUNT, base64.b64encode(key).decode("ascii"))
    except Exception:
        logger.exception("Failed to store encryption key in OS keyring")


def _os_store_has_backend() -> bool:
    """True when the keyring has a backend that accepts reads.

    Only called after :func:`get_from_os_store` returned ``None`` without
    raising, to tell "the store is empty" from "the store cannot be used at
    all". A key must only ever be minted in the first case.
    """
    kr = keyring
    if kr is None:
        return False
    try:
        kr.get_password(SERVICE, ACCOUNT)
    except Exception as exc:
        if _keyring_has_no_backend(exc):
            return False
        raise KeystoreUnavailableError(
            "OS keyring is enabled but could not be read.",
        ) from exc
    return True


def get_or_create_os_key() -> bytes | None:
    """Return an existing OS-store key or generate and store one.

    Returns:
        The 32-byte raw key if OS keyring is enabled or None if the feature
        is not available/disabled.

    Raises:
        KeystoreUnavailableError: the store is enabled but could not be read.
            A replacement key is deliberately *not* generated in that case —
            see :func:`get_from_os_store`.
    """
    if not _env_os_keystore_enabled() or not _os_keyring_available():
        return None

    key = get_from_os_store()
    if key is not None:
        return key

    # ``get_from_os_store`` returns None both when the store is empty and when
    # it has no usable backend. Only the first may create a key: minting one
    # into a keyring that cannot persist it would yield a different key on
    # every run and quietly lose data. A read *failure* already raised above.
    if not _os_store_has_backend():
        return None

    key = os.urandom(32)
    set_in_os_store(key)
    return key
