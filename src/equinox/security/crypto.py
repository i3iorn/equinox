"""Security-focused crypto helpers moved from core.crypto.

This module implements key management and Fernet construction, preferring
an OS-backed key when available (via the keystore) and falling back to the
local key file when not.
"""

from __future__ import annotations

import base64
import logging
import os
import tempfile
from pathlib import Path

from cryptography.fernet import Fernet

from equinox.security.keystore import get_or_create_os_key

logger = logging.getLogger(__name__)

__all__ = [
    "KEY_SIZE",
    "default_key_path",
    "key_file_valid",
    "get_or_create_raw_key",
    "make_fernet",
    "get_or_create_fernet",
]

# Number of raw bytes used as the encryption key (Fernet needs exactly 32).
KEY_SIZE = 32


def default_key_path() -> Path:
    """Return the default key file path (``~/.equinox/.key``)."""
    return Path.home() / ".equinox" / ".key"


def key_file_valid(key_path: Path | None = None) -> bool:
    """Return ``True`` if *key_path* exists and contains exactly :data:`KEY_SIZE` bytes.

    This is a lightweight probe that reads only the file size — it does not
    parse or load the key.  Useful for health checks and diagnostics without
    the side-effect of creating the key.
    """
    if key_path is None:
        key_path = default_key_path()
    try:
        return key_path.is_file() and key_path.stat().st_size == KEY_SIZE
    except OSError:
        return False


def get_or_create_raw_key(key_path: Path | None = None) -> bytes:
    """Read or generate a :data:`KEY_SIZE`-byte raw encryption key.

    * If *key_path* already exists the file is read and its length is
      verified.  A :exc:`RuntimeError` is raised for corrupt files so the
      caller is never silently handed a short key.
    * If *key_path* does not exist a new key is written atomically via a
      temporary file in the same directory (``os.replace`` guarantees
      all-or-nothing on both POSIX and Windows).

    The key file is created with ``0o600`` permissions (owner read/write
    only).  On platforms that do not support ``chmod`` the warning is logged
    but the key is still returned.
    """
    if key_path is None:
        key_path = default_key_path()

    # Prefers OS-keyring key when enabled; otherwise fall back to local key file.
    if get_or_create_os_key is not None:
        os_key = get_or_create_os_key()
        if os_key is not None:
            logger.debug("Using OS-backed encryption key from keyring (%d bytes)", len(os_key))
            return os_key

    key_path.parent.mkdir(parents=True, exist_ok=True)

    if key_path.exists():
        logger.debug("Loading encryption key from %s", key_path)
        key = key_path.read_bytes()
        if len(key) != KEY_SIZE:
            logger.error(
                "Encryption key is corrupt: expected %d bytes, got %d",
                KEY_SIZE,
                len(key),
            )
            raise RuntimeError(
                f"Corrupt encryption key at {key_path} (expected {KEY_SIZE} bytes, got {len(key)})",
            )
        logger.debug("Encryption key loaded successfully (%d bytes)", KEY_SIZE)
        return key

    logger.info("Generating new encryption key at %s", key_path)
    key = os.urandom(KEY_SIZE)

    # Write atomically: create a temp file in the same directory (so the
    # rename stays on the same filesystem), fsync, then replace.
    fd, tmp_str = tempfile.mkstemp(dir=str(key_path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(key)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_str, key_path)
    except BaseException:
        try:
            os.unlink(tmp_str)
        except OSError:
            pass
        raise

    try:
        os.chmod(key_path, 0o600)
        logger.debug("Encryption key file permissions set to 0o600")
    except (OSError, NotImplementedError):
        logger.warning("Could not set restrictive permissions on %s", key_path)

    logger.info("Encryption key generated and saved successfully")
    return key


def make_fernet(key_bytes: bytes) -> Fernet:
    """Return a :class:`~cryptography.fernet.Fernet` instance for *key_bytes*.

    *key_bytes* must be exactly :data:`KEY_SIZE` raw bytes.  This function
    performs the ``base64.urlsafe_b64encode`` step required by Fernet so
    callers do not have to.

    :raises ValueError: if *key_bytes* is not exactly :data:`KEY_SIZE` bytes.
    """
    if len(key_bytes) != KEY_SIZE:
        raise ValueError(f"key_bytes must be exactly {KEY_SIZE} bytes, got {len(key_bytes)}")
    return Fernet(base64.urlsafe_b64encode(key_bytes))


def get_or_create_fernet(key_path: Path | None = None) -> Fernet:
    """Convenience wrapper: read/generate the key then return a Fernet cipher.

    Equivalent to ``make_fernet(get_or_create_raw_key(key_path))`` but
    expressed as a single call for callers that do not need the raw key bytes.
    """
    return make_fernet(get_or_create_raw_key(key_path))
