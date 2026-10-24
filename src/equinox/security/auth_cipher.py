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
# Whether _fernet was derived from a master password rather than the file key.
# A file-key instance must not shadow a loader that supplies the master
# password, or auth data stays encrypted under ~/.equinox/.key while the user
# believes the master password protects it.
_fernet_from_master_password = False
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
    """Return a cached Fernet instance with optional master-password override.

    A cached master-password Fernet always wins. A cached *file-key* Fernet,
    however, must not shadow *master_password_loader*: when that loader is
    given and can produce a key, its result takes precedence. Otherwise a
    Fernet cached early in startup would keep auth data encrypted under the
    file key even though a master password is configured.
    """
    global _fernet, _fernet_from_master_password
    cached = _fernet
    if cached is not None and (_fernet_from_master_password or master_password_loader is None):
        return cached

    with _fernet_lock:
        cached = _fernet
        if cached is not None and (_fernet_from_master_password or master_password_loader is None):
            return cached

        loaded = _load_master_password_fernet(master_password_loader)
        if loaded is not None:
            _fernet = loaded
            _fernet_from_master_password = True
            return _fernet

        # The loader was absent or reported "no master password configured".
        # An existing cache is already the file key, so reuse it; only build a
        # new file-key Fernet when nothing has been cached yet.
        if cached is not None:
            return cached

        logger.warning(
            "Encryption is active WITHOUT a master password: secrets are protected "
            "by the file key in ~/.equinox/.key, which lives beside the database. "
            "Any backup or sync of the home directory exfiltrates key and ciphertext "
            "together (issue #66). Set EQUINOX_MASTER_PASSWORD or configure a master "
            "password in the app to protect secrets with PBKDF2 derivation instead.",
        )

        key = get_or_create_key()
        fernet_obj = crypto.make_fernet(key)
        if not isinstance(fernet_obj, Fernet):
            raise SecurityError("Crypto backend returned invalid Fernet instance")
        _fernet = fernet_obj
        _fernet_from_master_password = False
        return _fernet


def _load_master_password_fernet(
    master_password_loader: Callable[[], Fernet | None] | None,
) -> Fernet | None:
    """Load a Fernet from a master-password callback when one is available.

    A loader returning ``None`` means "no master password configured" and is a
    normal fallback to the file key. A loader that *raises*, by contrast, is
    surfaced rather than swallowed: silently falling back would encrypt data
    under the file key while the user believes the master password protects it.
    """
    if master_password_loader is None:
        return None
    try:
        return master_password_loader()
    except Exception as exc:
        logger.error(
            "Master password loader failed; refusing to fall back to the file key",
            exc_info=True,
        )
        raise SecurityError(
            "Master password could not be loaded; secret encryption aborted.",
            details={"error": type(exc).__name__},
            hint_key="auth_failed",
        ) from exc


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
    global _fernet, _fernet_from_master_password
    _fernet = None
    _fernet_from_master_password = False
