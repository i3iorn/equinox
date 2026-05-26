"""Storage-layer helpers for auth-data column encoding/decoding.

These helpers own the ``enc:`` storage format contract while delegating
cryptographic operations to :mod:`equinox.security.auth_cipher`.
"""

from __future__ import annotations

import logging

from equinox.security.auth_cipher import decode_utf8, decrypt_token_to_bytes, encrypt_utf8
from equinox.security.secrets_password import ensure_master_password_initialized

_ENC_PREFIX = "enc:"
logger = logging.getLogger(__name__)


def is_nonempty_string(value: str | None) -> bool:
    """Return True when value is a non-empty string."""
    return isinstance(value, str) and bool(value)


def is_encrypted_value(stored: str) -> bool:
    """Return True when stored value uses the encrypted prefix."""
    return stored.startswith(_ENC_PREFIX)


def strip_encryption_prefix(stored: str) -> str:
    """Return ciphertext without the storage prefix."""
    return stored[len(_ENC_PREFIX) :]


def add_encryption_prefix(ciphertext: str) -> str:
    """Return storage-formatted encrypted value with required prefix."""
    return _ENC_PREFIX + ciphertext


def encrypt_auth_storage_value(plaintext: str) -> str:
    """Encrypt plaintext for auth/config storage columns."""
    ciphertext = encrypt_utf8(
        plaintext,
        master_password_loader=ensure_master_password_initialized,
    )
    return add_encryption_prefix(ciphertext)


def decrypt_auth_storage_value(stored: str | None, field_name: str = "auth_data") -> str | None:
    """Decode auth/config column value with plaintext legacy fallback."""
    if not is_nonempty_string(stored):
        return stored

    if not is_encrypted_value(stored):
        logger.debug("Returning plaintext (legacy) for %s", field_name)
        return stored

    ciphertext = strip_encryption_prefix(stored)
    plaintext_bytes = decrypt_token_to_bytes(
        ciphertext,
        field_name,
        master_password_loader=ensure_master_password_initialized,
    )
    return decode_utf8(plaintext_bytes, field_name)
