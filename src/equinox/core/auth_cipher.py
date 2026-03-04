"""Column-level encryption for auth credentials stored in SQLite.

Every ``auth_data`` and ``saved_credentials.config`` value passes through
:func:`encrypt_auth_data` on write and :func:`decrypt_auth_data` on read.

Encrypted values carry an ``enc:`` prefix so the reader can transparently
fall back to plaintext for databases created before encryption was enabled
(graceful migration — no schema change required).

The encryption key is the **same** 32-byte key used by
:class:`~equinox.core.secure_storage.SecureStorage` and is stored at
``~/.equinox/.key``.  If the key file does not yet exist it is created
automatically with restrictive permissions.
"""

import base64
import logging
import os
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

_EQUINOX_DIR = Path.home() / ".equinox"
_KEY_PATH = _EQUINOX_DIR / ".key"

# Module-level singleton — created lazily by _get_fernet().
_fernet: Optional[Fernet] = None

# Prefix that distinguishes encrypted blobs from legacy plaintext JSON.
_ENC_PREFIX = "enc:"


def _get_fernet() -> Fernet:
    """Return (and cache) a Fernet cipher backed by ``~/.equinox/.key``."""
    global _fernet
    if _fernet is not None:
        return _fernet

    _EQUINOX_DIR.mkdir(parents=True, exist_ok=True)

    if _KEY_PATH.exists():
        key = _KEY_PATH.read_bytes()
        if len(key) != 32:
            raise RuntimeError(
                f"Corrupt encryption key at {_KEY_PATH} "
                f"(expected 32 bytes, got {len(key)})"
            )
    else:
        key = os.urandom(32)
        _KEY_PATH.write_bytes(key)
        try:
            os.chmod(_KEY_PATH, 0o600)
        except (OSError, NotImplementedError):
            logger.warning("Could not set restrictive permissions on %s", _KEY_PATH)

    _fernet = Fernet(base64.urlsafe_b64encode(key))
    return _fernet


# ── Public API ────────────────────────────────────────────────────────────────


def encrypt_auth_data(plaintext_json: str) -> str:
    """Encrypt a JSON string for storage in an ``auth_data`` / ``config`` column.

    Returns a string prefixed with ``enc:`` followed by the Fernet ciphertext
    (URL-safe base-64).
    """
    f = _get_fernet()
    token = f.encrypt(plaintext_json.encode("utf-8"))
    return _ENC_PREFIX + token.decode("ascii")


def decrypt_auth_data(stored: str) -> str:
    """Decrypt an ``auth_data`` / ``config`` column value.

    If *stored* starts with ``enc:`` the remainder is decrypted via Fernet.
    Otherwise the value is returned as-is (legacy plaintext — graceful
    migration).
    """
    if not stored:
        return stored
    if stored.startswith(_ENC_PREFIX):
        f = _get_fernet()
        try:
            plaintext = f.decrypt(stored[len(_ENC_PREFIX):].encode("ascii"))
            return plaintext.decode("utf-8")
        except InvalidToken:
            logger.error(
                "Failed to decrypt auth data — key mismatch or corrupt data. "
                "Returning empty object."
            )
            return "{}"
    # Legacy plaintext
    return stored


def reset_cipher() -> None:
    """Clear the cached Fernet instance (useful for testing)."""
    global _fernet
    _fernet = None

