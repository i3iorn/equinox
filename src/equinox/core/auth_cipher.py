"""Column-level encryption for auth credentials stored in SQLite.

Every ``auth_data`` and ``saved_credentials.config`` value passes through
:func:`encrypt_auth_data` on write and :func:`decrypt_auth_data` on read.

Encrypted values carry an ``enc:`` prefix so the reader can transparently
fall back to plaintext for databases created before encryption was enabled
(graceful migration — no schema change required).

Encryption uses Fernet (AES-128-CBC + HMAC-SHA256).  The 32-byte raw key
is stored at ``~/.equinox/.key`` and is shared with
:class:`~equinox.security.secure_storage.SecureStorage`.  If the key file does
not yet exist it is created automatically with restrictive permissions.
"""

import base64
import logging
import threading
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

from equinox.security import crypto
from equinox.security.secrets_password import ensure_master_password_initialized
from equinox.core.exceptions import SecurityError

logger = logging.getLogger(__name__)

# Module-level singleton — created lazily by _get_fernet().
_fernet: Optional[Fernet] = None
_fernet_lock = threading.Lock()

# Prefix that distinguishes encrypted blobs from legacy plaintext JSON.
_ENC_PREFIX = "enc:"


def get_or_create_key(key_path: Optional[Path] = None) -> bytes:
    """Compatibility wrapper that delegates to :mod:`equinox.security.crypto`.

    Keeps the original public function name so callers in the codebase
    continue to work while the canonical implementation lives in
    :mod:`equinox.security.crypto`.
    """
    return crypto.get_or_create_raw_key(key_path)


def _get_fernet() -> Fernet:
    """Return (and cache) a Fernet cipher backed by ``~/.equinox/.key``.

    Thread-safe: uses double-checked locking to avoid races during
    first initialisation while keeping the hot path lock-free.
    """
    global _fernet
    if _fernet is not None:
        return _fernet
    with _fernet_lock:
        if _fernet is not None:
            return _fernet
        # Prefer master-password derived key if configured
        try:
            f = ensure_master_password_initialized()
        except Exception:
            f = None
        if f is not None:
            _fernet = f
            return _fernet
        # Fallback to legacy key-based path
        key = get_or_create_key()
        _fernet = crypto.make_fernet(key)
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
        except InvalidToken as exc:
            # Surface decryption failures loudly — returning a silent empty
            # blob hides key-rotation problems and corrupt data.  Callers
            # should handle SecurityError explicitly.
            logger.exception(
                "Failed to decrypt auth data — key mismatch or corrupt data.")
            raise SecurityError(
                "Failed to decrypt stored auth data: key mismatch or corrupt data"
            ) from exc
    # Legacy plaintext
    return stored


def reset_cipher() -> None:
    """Clear the cached Fernet instance (useful for testing)."""
    global _fernet
    _fernet = None

