"""Secure credential storage using encryption (refactored, single-module)"""

from __future__ import annotations

import base64
import ctypes
import gc
import json
import logging
import os
import tempfile
import threading
from pathlib import Path
from typing import Any, TypedDict

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from equinox.core.audit import get_audit_logger
from equinox.core.exceptions import SecurityError, ValidationError
from equinox.security import crypto

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Typed structures
# ──────────────────────────────────────────────────────────────────────────────


class CredentialEntry(TypedDict):
    value: str
    metadata: dict[str, Any]


class ExportPayloadV2(TypedDict):
    version: int
    kdf: str
    kdf_params: dict[str, Any]
    salt: str
    data: str


# ──────────────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────────────

MAX_KEY_LEN = 256
MAX_VALUE_LEN = 10_000

# File permissions: read/write for owner only
_FILE_PERMISSIONS = 0o600

# Derived key length for all KDF algorithms
_KEY_LENGTH = 32

# Scrypt parameters: cost (N), block size (r), parallelization (p)
_SCRYPT_N = 2**17
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_SALT_LEN = 32

# PBKDF2 parameters
_PBKDF2_ITERATIONS = 1_000_000

# JSON serialization format (compact, no whitespace)
_JSON_COMPACT = {"separators": (",", ":")}
_JSON_PRETTY = {"indent": 2}

# Export format version
_EXPORT_VERSION_V2 = 2


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────


def _ensure_permissions(path: Path) -> None:
    """Set file permissions to owner-read/write only (0o600)."""
    try:
        os.chmod(path, _FILE_PERMISSIONS)
    except (OSError, NotImplementedError):
        logger.debug("chmod not supported on this platform")


def _atomic_write(path: Path, data: bytes) -> None:
    """Write *data* to *path* atomically via a temporary file."""
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")

    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())

        os.replace(tmp_path, path)

    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _wipe_bytes(b: bytes | None) -> None:
    """Best-effort overwrite of *b* in memory.

    CPython may have copied the bytes elsewhere (interning, buffer copies)
    so this is **not** a guarantee that the secret is erased from process
    memory.  It reduces the window of exposure, however.
    """
    if not b:
        return
    try:
        ptr = ctypes.cast(id(b), ctypes.POINTER(ctypes.c_char))
        offset = bytes.__basicsize__
        for i in range(len(b)):
            ptr[offset + i] = b"\x00"
    except Exception:
        logger.debug("_wipe_bytes: ctypes overwrite failed (non-critical)")
    finally:
        del b
        gc.collect()


def _validate_key_value(key: str, value: str | None = None) -> None:
    """Validate a credential key and optional value for length and type."""
    if not key or not isinstance(key, str):
        raise ValidationError("Credential key must be a non-empty string")

    if len(key) > MAX_KEY_LEN:
        raise ValidationError("Credential key too long")

    if value is not None:
        if not isinstance(value, str):
            raise ValidationError("Credential value must be a string")

        if len(value) > MAX_VALUE_LEN:
            raise ValidationError("Credential value too long")


def _serialize_json(obj: Any, pretty: bool = False) -> str:
    """Serialize *obj* to JSON string (compact or pretty-printed)."""
    kwargs = _JSON_PRETTY if pretty else _JSON_COMPACT
    return json.dumps(obj, **kwargs)


def _serialize_json_bytes(obj: Any, pretty: bool = False) -> bytes:
    """Serialize *obj* to JSON bytes (compact or pretty-printed)."""
    return _serialize_json(obj, pretty=pretty).encode("utf-8")


def _encode_key_for_fernet(key: bytes) -> bytes:
    """Base64-encode a derived key for use with Fernet."""
    return base64.urlsafe_b64encode(key)


# ──────────────────────────────────────────────────────────────────────────────
# Secure Storage
# ──────────────────────────────────────────────────────────────────────────────


class SecureStorage:
    """Secure credential storage with encryption."""

    def __init__(self, storage_path: Path | None = None):
        self.storage_path = Path(storage_path or Path.home() / ".equinox" / ".credentials")

        self.storage_path.parent.mkdir(parents=True, exist_ok=True)

        if self.storage_path.exists():
            _ensure_permissions(self.storage_path)

        self._cipher: Fernet | None = None
        self._audit = get_audit_logger()
        # Protects all read-modify-write operations so concurrent calls from
        # multiple threads do not silently clobber each other's changes.
        self._lock = threading.Lock()

    # ──────────────────────────────────────────────────────────────────────────
    # Key + Cipher
    # ──────────────────────────────────────────────────────────────────────────

    def _get_key(self) -> bytes:
        """Load or create the master encryption key."""
        local = self.storage_path.parent / ".key"
        key_path = local if local.exists() else crypto.default_key_path()

        try:
            return crypto.get_or_create_raw_key(key_path)
        except Exception as exc:
            logger.exception("Key generation failed")
            raise SecurityError("Failed to load encryption key") from exc

    def _get_cipher(self) -> Fernet:
        """Return a cached Fernet cipher, creating it on first call."""
        if self._cipher is None:
            try:
                self._cipher = crypto.make_fernet(self._get_key())
            except Exception as exc:
                logger.exception("Cipher initialization failed")
                raise SecurityError("Failed to initialize encryption") from exc
        return self._cipher

    # ──────────────────────────────────────────────────────────────────────────
    # Storage I/O
    # ──────────────────────────────────────────────────────────────────────────

    def _load(self) -> dict[str, CredentialEntry]:
        """Load and decrypt credentials from storage."""
        if not self.storage_path.exists():
            return {}

        try:
            cipher = self._get_cipher()

            data = self.storage_path.read_bytes()
            if not data:
                return {}

            decrypted = cipher.decrypt(data)
            result = json.loads(decrypted.decode("utf-8"))

            if not isinstance(result, dict):
                raise SecurityError("Invalid storage format")

            return result

        except Exception as exc:
            logger.exception("Failed to load storage")
            raise SecurityError("Failed to decrypt credentials") from exc

    def _save(self, storage: dict[str, CredentialEntry]) -> None:
        """Encrypt and save credentials to storage."""
        try:
            cipher = self._get_cipher()

            payload = _serialize_json_bytes(storage)
            encrypted = cipher.encrypt(payload)

            _atomic_write(self.storage_path, encrypted)
            _ensure_permissions(self.storage_path)

        except Exception as exc:
            logger.exception("Failed to save storage")
            raise SecurityError("Failed to encrypt credentials") from exc

    # ──────────────────────────────────────────────────────────────────────────
    # CRUD
    # ──────────────────────────────────────────────────────────────────────────

    def store(
        self,
        key: str,
        value: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Store a credential."""
        _validate_key_value(key, value)

        with self._lock:
            storage = self._load()
            storage[key] = {
                "value": value,
                "metadata": metadata or {},
            }
            self._save(storage)

        self._audit.log_credential_access("store", key)

    def retrieve(self, key: str) -> str | None:
        """Retrieve a credential by key."""
        _validate_key_value(key)

        with self._lock:
            storage = self._load()
            entry = storage.get(key)

        if entry is None:
            return None

        self._audit.log_credential_access("retrieve", key)
        return entry.get("value")

    def delete(self, key: str) -> bool:
        """Delete a credential; return True if it existed."""
        _validate_key_value(key)

        with self._lock:
            storage = self._load()
            if key not in storage:
                return False
            del storage[key]
            self._save(storage)

        self._audit.log_credential_access("delete", key)
        return True

    def list_keys(self) -> list[str]:
        """List all stored credential keys."""
        with self._lock:
            return list(self._load().keys())

    def clear(self) -> None:
        """Clear all stored credentials."""
        with self._lock:
            self._save({})
        logger.warning("All credentials cleared")

    # ──────────────────────────────────────────────────────────────────────────
    # Export / Import
    # ──────────────────────────────────────────────────────────────────────────

    def export_encrypted(self, path: Path, password: str) -> None:
        """Export credentials to an encrypted file."""
        self._validate_password(password)

        with self._lock:
            storage = self._load()
        key: bytes | None = None

        try:
            salt = os.urandom(_SCRYPT_SALT_LEN)
            key = self._derive_scrypt(password, salt)

            cipher = Fernet(key)
            encrypted = cipher.encrypt(_serialize_json_bytes(storage))

            payload: ExportPayloadV2 = {
                "version": _EXPORT_VERSION_V2,
                "kdf": "scrypt",
                "kdf_params": {
                    "n": _SCRYPT_N,
                    "r": _SCRYPT_R,
                    "p": _SCRYPT_P,
                },
                "salt": base64.b64encode(salt).decode("ascii"),
                "data": base64.b64encode(encrypted).decode("ascii"),
            }

            path.write_text(_serialize_json(payload, pretty=True), encoding="utf-8")

            self._audit.log_credential_access("export", str(path))

        except Exception as exc:
            logger.exception("Export failed")
            raise SecurityError("Failed to export credentials") from exc

        finally:
            _wipe_bytes(key)

    def import_encrypted(self, path: Path, password: str) -> int:
        """Import credentials from an encrypted file."""
        if not path.exists():
            raise ValidationError(f"File not found: {path}")

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ValidationError("Cannot read export file") from exc

        # Validate required export fields early
        version = payload.get("version", 1)
        if version >= _EXPORT_VERSION_V2:
            if not payload.get("salt") or not payload.get("data"):
                raise ValidationError("Export missing required fields")

        salt = base64.b64decode(payload.get("salt", ""))
        encrypted = base64.b64decode(payload.get("data", ""))

        key: bytes | None = None

        try:
            if version >= _EXPORT_VERSION_V2:
                key = self._derive_scrypt(password, salt)
            else:
                key = self._derive_pbkdf2(password, salt)

            cipher = Fernet(key)
            decrypted = cipher.decrypt(encrypted)
            imported = json.loads(decrypted.decode())

        except Exception as exc:
            raise SecurityError("Decryption failed") from exc

        finally:
            _wipe_bytes(key)

        if not isinstance(imported, dict):
            raise ValidationError("Invalid payload")

        with self._lock:
            storage = self._load()
            storage.update(imported)
            self._save(storage)

        self._audit.log_credential_access("import", str(path))
        return len(imported)

    # ──────────────────────────────────────────────────────────────────────────
    # Crypto helpers
    # ──────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _validate_password(password: str) -> None:
        """Validate password strength (≥12 chars, ≥3 character classes)."""
        if not password or len(password) < 12:
            raise ValidationError("Password must be at least 12 characters")

        # Count character classes: uppercase, lowercase, digit, symbol
        has_upper = any(c.isupper() for c in password)
        has_lower = any(c.islower() for c in password)
        has_digit = any(c.isdigit() for c in password)
        has_symbol = any(not c.isalnum() for c in password)

        classes = sum([has_upper, has_lower, has_digit, has_symbol])

        if classes < 3:
            raise ValidationError(
                "Password too weak: must include at least 3 of uppercase, lowercase, digits, or symbols"
            )

    @staticmethod
    def _derive_scrypt(password: str, salt: bytes) -> bytes:
        """Derive an encryption key using Scrypt."""
        kdf = Scrypt(
            salt=salt,
            length=_KEY_LENGTH,
            n=_SCRYPT_N,
            r=_SCRYPT_R,
            p=_SCRYPT_P,
        )
        return _encode_key_for_fernet(kdf.derive(password.encode()))

    @staticmethod
    def _derive_pbkdf2(password: str, salt: bytes) -> bytes:
        """Derive an encryption key using PBKDF2-SHA256 (legacy)."""
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=_KEY_LENGTH,
            salt=salt,
            iterations=_PBKDF2_ITERATIONS,
        )
        return _encode_key_for_fernet(kdf.derive(password.encode()))
