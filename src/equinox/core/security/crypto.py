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
from typing import Optional

from cryptography.fernet import Fernet

from equinox.core.keystore import get_or_create_os_key

logger = logging.getLogger(__name__)

__all__ = [
    "KEY_SIZE",
    "default_key_path",
    "key_file_valid",
    "get_or_create_raw_key",
    "make_fernet",
    "get_or_create_fernet",
]

# Fernet requires a 32-byte raw key
KEY_SIZE = 32


def default_key_path() -> Path:
    return Path.home() / ".equinox" / ".key"


def key_file_valid(key_path: Optional[Path] = None) -> bool:
    if key_path is None:
        key_path = default_key_path()
    try:
        return key_path.is_file() and key_path.stat().st_size == KEY_SIZE
    except OSError:
        return False


def get_or_create_raw_key(key_path: Optional[Path] = None) -> bytes:
    """Load or create the 32-byte raw key.

    Prefers OS keyring if available; otherwise uses the local file at
    ~/.equinox/.key.
    """
    if key_path is None:
        key_path = default_key_path()

    # Try OS-backed key first
    if get_or_create_os_key is not None:
        os_key = get_or_create_os_key()
        if os_key is not None:
            logger.debug("Using OS-backed encryption key from keyring (%d bytes)", len(os_key))
            return os_key

    key_path.parent.mkdir(parents=True, exist_ok=True)

    if key_path.exists():
        key = key_path.read_bytes()
        if len(key) != KEY_SIZE:
            raise RuntimeError(
                f"Corrupt encryption key at {key_path} (expected {KEY_SIZE} bytes, got {len(key)})"
            )
        return key

    key = os.urandom(KEY_SIZE)
    fd, tmp_path = tempfile.mkstemp(dir=str(key_path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(key)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, key_path)
    finally:
        try:
            os.chmod(key_path, 0o600)
        except Exception:
            pass
    return key


def make_fernet(key_bytes: bytes) -> Fernet:
    if len(key_bytes) != KEY_SIZE:
        raise ValueError(f"key_bytes must be exactly {KEY_SIZE} bytes, got {len(key_bytes)}")
    return Fernet(base64.urlsafe_b64encode(key_bytes))


def get_or_create_fernet(key_path: Optional[Path] = None) -> Fernet:
    return make_fernet(get_or_create_raw_key(key_path))
