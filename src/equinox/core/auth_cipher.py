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


def get_or_create_key(key_path: Optional[Path] = None) -> bytes:
    """Read or generate a 32-byte encryption key at *key_path*.

    This is the canonical key-management function for the entire
    application.  Both ``auth_cipher`` and
    :class:`~equinox.core.secure_storage.SecureStorage` delegate here
    so the logic is never duplicated.

    Args:
        key_path: Path to the key file.  Defaults to ``~/.equinox/.key``.

    Returns:
        32 raw bytes suitable for deriving a Fernet key via
        ``base64.urlsafe_b64encode``.

    Raises:
        RuntimeError: If an existing key file is corrupt.
    """
    if key_path is None:
        key_path = _KEY_PATH

    key_path.parent.mkdir(parents=True, exist_ok=True)

    if key_path.exists():
        logger.debug("Loading encryption key from %s", key_path)
        key = key_path.read_bytes()
        if len(key) != 32:
            logger.error("Encryption key is corrupt: expected 32 bytes, got %d", len(key))
            raise RuntimeError(
                f"Corrupt encryption key at {key_path} "
                f"(expected 32 bytes, got {len(key)})"
            )
        logger.debug("Encryption key loaded successfully (%d bytes)", len(key))
        return key

    logger.info("Generating new encryption key at %s", key_path)
    key = os.urandom(32)
    key_path.write_bytes(key)
    try:
        os.chmod(key_path, 0o600)
        logger.debug("Encryption key file permissions set to 0o600")
    except (OSError, NotImplementedError):
        logger.warning("Could not set restrictive permissions on %s", key_path)
    logger.info("Encryption key generated and saved successfully")
    return key


def _get_fernet() -> Fernet:
    """Return (and cache) a Fernet cipher backed by ``~/.equinox/.key``."""
    global _fernet
    if _fernet is not None:
        return _fernet

    key = get_or_create_key()
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

