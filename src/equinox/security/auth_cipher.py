"""Security primitives for auth credential encryption and decryption."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path

from cryptography.fernet import Fernet
from cryptography.fernet import InvalidToken
from equinox.core.exceptions import SecurityError
from equinox.security import crypto

logger = logging.getLogger(__name__)

_fernet: Fernet | None = None
_fernet_lock = threading.Lock()


def get_or_create_key(key_path: Path | None = None) -> bytes:
    """Return the raw key used for auth-data encryption."""
    key = crypto.get_or_create_raw_key(key_path)
    if isinstance(key, bytes):
        return key
    raise SecurityError("Crypto backend returned non-bytes key material")


def get_fernet(
    master_password_loader: Callable[[], Fernet | None] | None = None,
) -> Fernet:
    """Return a cached Fernet instance with optional master-password override."""
    global _fernet
    cached = _fernet
    if cached is not None:
        return cached

    with _fernet_lock:
        cached = _fernet
        if cached is not None:
            return cached

        loaded = _load_master_password_fernet(master_password_loader)
        if loaded is not None:
            _fernet = loaded
            return _fernet

        key = get_or_create_key()
        fernet_obj = crypto.make_fernet(key)
        if not isinstance(fernet_obj, Fernet):
            raise SecurityError("Crypto backend returned invalid Fernet instance")
        _fernet = fernet_obj
        return _fernet


def _load_master_password_fernet(
    master_password_loader: Callable[[], Fernet | None] | None,
) -> Fernet | None:
    """Load Fernet from a master-password callback when available."""
    if master_password_loader is None:
        return None
    try:
        return master_password_loader()
    except Exception:
        return None


def encrypt_utf8(
    plaintext: str,
    master_password_loader: Callable[[], Fernet | None] | None = None,
) -> str:
    """Encrypt a UTF-8 plaintext string and return ASCII Fernet token text."""
    f = get_fernet(master_password_loader=master_password_loader)
    token = f.encrypt(plaintext.encode("utf-8"))
    if not isinstance(token, bytes):
        raise SecurityError("Encryption backend returned non-bytes token")
    return token.decode("ascii")


def decrypt_token_to_bytes(
    ciphertext: str,
    field_name: str,
    master_password_loader: Callable[[], Fernet | None] | None = None,
) -> bytes:
    """Decrypt ASCII ciphertext and return raw plaintext bytes."""
    f = get_fernet(master_password_loader=master_password_loader)
    try:
        plaintext = f.decrypt(ciphertext.encode("ascii"))
        if isinstance(plaintext, bytes):
            return plaintext
        raise SecurityError("Decryption backend returned non-bytes payload")
    except InvalidToken as exc:
        logger.error(
            "Failed to decrypt %s: ciphertext is invalid or corrupted",
            field_name,
            extra={
                "field": field_name,
                "ciphertext_length": len(ciphertext),
                "error": type(exc).__name__,
            },
        )
        raise SecurityError(
            f"Failed to decrypt {field_name}: token is invalid or corrupted. "
            f"This may indicate a key mismatch or corrupted data.",
            details={
                "field": field_name,
                "ciphertext_length": len(ciphertext),
                "error_type": type(exc).__name__,
            },
            hint_key="auth_failed",
        ) from exc
    except Exception as exc:
        logger.error(
            "Unexpected error decrypting %s: %s",
            field_name,
            str(exc),
            extra={"field": field_name, "error": str(exc)},
            exc_info=True,
        )
        raise SecurityError(
            f"Unexpected error decrypting {field_name}",
            details={"field": field_name, "error": str(exc)},
        ) from exc


def decode_utf8(plaintext_bytes: bytes, field_name: str) -> str:
    """Decode UTF-8 plaintext bytes and raise SecurityError on corruption."""
    try:
        return plaintext_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        logger.error(
            "Failed to decode decrypted %s as UTF-8",
            field_name,
            extra={"field": field_name, "error": str(exc)},
        )
        raise SecurityError(
            f"Decrypted {field_name} is not valid UTF-8. Data may be corrupted.",
            details={"field": field_name, "error": str(exc)},
        ) from exc


def reset_fernet_cache() -> None:
    """Clear cached Fernet instance (used by tests)."""
    global _fernet
    _fernet = None
