"""Compatibility facade for auth-data encryption/decryption.

This module intentionally keeps only boundary orchestration and legacy API
names. Strict cryptographic primitives live in
:mod:`equinox.security.auth_cipher`, while storage encoding/prefix handling
lives in :mod:`equinox.storage.auth_cipher_storage`.
"""

from __future__ import annotations

import logging
from pathlib import Path

from equinox.security.auth_cipher import (
    decode_utf8,
    decrypt_token_to_bytes,
    encrypt_utf8,
    reset_fernet_cache,
)
from equinox.security.auth_cipher import (
    get_or_create_key as _get_or_create_key,
)
from equinox.security.secrets_password import ensure_master_password_initialized
from equinox.storage.auth_cipher_storage import (
    add_encryption_prefix,
    is_encrypted_value,
    is_nonempty_string,
    strip_encryption_prefix,
)

logger = logging.getLogger(__name__)


def get_or_create_key(key_path: Path | None = None) -> bytes:
    """Compatibility wrapper for key creation used by legacy callers."""
    return _get_or_create_key(key_path)


def encrypt_auth_data(plaintext_json: str) -> str:
    """Encrypt JSON data for ``auth_data``/``config`` storage columns."""
    ciphertext = encrypt_utf8(
        plaintext_json,
        master_password_loader=ensure_master_password_initialized,
    )
    return add_encryption_prefix(ciphertext)


def decrypt_auth_data(stored: str | None, field_name: str = "auth_data") -> str | None:
    """Decrypt auth/config column value with plaintext legacy fallback."""
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


def reset_cipher() -> None:
    """Clear the cached Fernet instance (useful for testing)."""
    reset_fernet_cache()
