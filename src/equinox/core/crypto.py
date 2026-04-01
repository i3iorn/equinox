"""Centralized crypto helpers for key management and Fernet creation.

This module consolidates key file location, atomic creation, permission
handling and Fernet construction so callers don't duplicate semantics.

Fernet uses AES-128-CBC for encryption and HMAC-SHA256 for authentication.
The 32-byte raw key is split by Fernet into a 16-byte signing key and a
16-byte encryption key.  Despite the 256-bit input, the *AES* key size
is 128 bits.
"""

import os
import logging
import base64
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet

logger = logging.getLogger(__name__)


def default_key_path() -> Path:
    """Return the default key file path (``~/.equinox/.key``)."""
    return Path.home() / ".equinox" / ".key"


def get_or_create_raw_key(key_path: Optional[Path] = None) -> bytes:
    """Read or generate a 32-byte raw encryption key.

    This mirrors the previous logic in `auth_cipher.get_or_create_key` but
    centralizes it for use across the application.
    """
    if key_path is None:
        key_path = default_key_path()

    key_path.parent.mkdir(parents=True, exist_ok=True)

    if key_path.exists():
        logger.debug("Loading encryption key from %s", key_path)
        key = key_path.read_bytes()
        if len(key) != 32:
            logger.error("Encryption key is corrupt: expected 32 bytes, got %d", len(key))
            raise RuntimeError(
                f"Corrupt encryption key at {key_path} (expected 32 bytes, got {len(key)})"
            )
        logger.debug("Encryption key loaded successfully (%d bytes)", len(key))
        return key

    logger.info("Generating new encryption key at %s", key_path)
    key = os.urandom(32)

    # Write atomically: write to a temp file in the same dir then replace.
    tmp_path = key_path.with_suffix(".tmp")
    try:
        with open(tmp_path, "wb") as fh:
            fh.write(key)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, str(key_path))
    finally:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except Exception:
            pass

    try:
        os.chmod(key_path, 0o600)
        logger.debug("Encryption key file permissions set to 0o600")
    except (OSError, NotImplementedError):
        logger.warning("Could not set restrictive permissions on %s", key_path)

    logger.info("Encryption key generated and saved successfully")
    return key


def make_fernet(key_bytes: bytes) -> Fernet:
    """Return a :class:`cryptography.fernet.Fernet` instance for *key_bytes*.

    The input must be raw 32 bytes; this function performs the
    ``base64.urlsafe_b64encode`` step required by Fernet.
    """
    return Fernet(base64.urlsafe_b64encode(key_bytes))

