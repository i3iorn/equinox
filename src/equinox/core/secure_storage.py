"""Secure credential storage using encryption (refactored, single-module)"""

from __future__ import annotations

import os
import json
import base64
import logging
import tempfile
import ctypes
import threading

from pathlib import Path
from typing import Optional, Dict, Any, List, TypedDict

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from equinox.core.exceptions import SecurityError, ValidationError
from equinox.core.audit import get_audit_logger
from equinox.core import crypto

logger = logging.getLogger(__name__)

# =========================================================
# Typed structures
# =========================================================

class CredentialEntry(TypedDict):
    value: str
    metadata: Dict[str, Any]


class ExportPayloadV2(TypedDict):
    version: int
    kdf: str
    kdf_params: Dict[str, Any]
    salt: str
    data: str


# =========================================================
# Constants
# =========================================================

MAX_KEY_LEN = 256
MAX_VALUE_LEN = 10_000

# =========================================================
# Helpers
# =========================================================

def _ensure_permissions(path: Path) -> None:
    try:
        os.chmod(path, 0o600)
    except (OSError, NotImplementedError):
        logger.debug("chmod not supported on this platform")


def _atomic_write(path: Path, data: bytes) -> None:
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


def _wipe_bytes(b: Optional[bytes]) -> None:
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
        import gc
        del b
        gc.collect()


def _validate_key_value(key: str, value: Optional[str] = None) -> None:
    if not key or not isinstance(key, str):
        raise ValidationError("Credential key must be a non-empty string")

    if len(key) > MAX_KEY_LEN:
        raise ValidationError("Credential key too long")

    if value is not None:
        if not isinstance(value, str):
            raise ValidationError("Credential value must be a string")

        if len(value) > MAX_VALUE_LEN:
            raise ValidationError("Credential value too long")


# =========================================================
# Secure Storage
# =========================================================

class SecureStorage:
    """Secure credential storage with encryption."""

    def __init__(self, storage_path: Optional[Path] = None):
        self.storage_path = Path(
            storage_path or Path.home() / ".equinox" / ".credentials"
        )

        self.storage_path.parent.mkdir(parents=True, exist_ok=True)

        if self.storage_path.exists():
            _ensure_permissions(self.storage_path)

        self._cipher: Optional[Fernet] = None
        self._audit = get_audit_logger()
        # Protects all read-modify-write operations so concurrent calls from
        # multiple threads do not silently clobber each other's changes.
        self._lock = threading.Lock()

    # =====================================================
    # Key + Cipher
    # =====================================================

    def _get_key(self) -> bytes:
        local = self.storage_path.parent / ".key"
        key_path = local if local.exists() else crypto.default_key_path()

        try:
            return crypto.get_or_create_raw_key(key_path)
        except Exception as exc:
            logger.exception("Key generation failed")
            raise SecurityError("Failed to load encryption key") from exc

    def _get_cipher(self) -> Fernet:
        if self._cipher is None:
            try:
                self._cipher = crypto.make_fernet(self._get_key())
            except Exception as exc:
                logger.exception("Cipher initialization failed")
                raise SecurityError("Failed to initialize encryption") from exc
        return self._cipher

    # =====================================================
    # Storage I/O
    # =====================================================

    def _load(self) -> Dict[str, CredentialEntry]:
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

    def _save(self, storage: Dict[str, CredentialEntry]) -> None:
        try:
            cipher = self._get_cipher()

            payload = json.dumps(storage, separators=(",", ":")).encode("utf-8")
            encrypted = cipher.encrypt(payload)

            _atomic_write(self.storage_path, encrypted)
            _ensure_permissions(self.storage_path)

        except Exception as exc:
            logger.exception("Failed to save storage")
            raise SecurityError("Failed to encrypt credentials") from exc

    # =====================================================
    # CRUD
    # =====================================================

    def store(
        self,
        key: str,
        value: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        _validate_key_value(key, value)

        with self._lock:
            storage = self._load()
            storage[key] = {
                "value": value,
                "metadata": metadata or {},
            }
            self._save(storage)

        self._audit.log_credential_access("store", key)

    def retrieve(self, key: str) -> Optional[str]:
        _validate_key_value(key)

        with self._lock:
            storage = self._load()
            entry = storage.get(key)

        if entry is None:
            return None

        self._audit.log_credential_access("retrieve", key)
        return entry.get("value")

    def delete(self, key: str) -> bool:
        _validate_key_value(key)

        with self._lock:
            storage = self._load()
            if key not in storage:
                return False
            del storage[key]
            self._save(storage)

        self._audit.log_credential_access("delete", key)
        return True

    def list_keys(self) -> List[str]:
        with self._lock:
            return list(self._load().keys())

    def clear(self) -> None:
        with self._lock:
            self._save({})
        logger.warning("All credentials cleared")

    # =====================================================
    # Export / Import
    # =====================================================

    def export_encrypted(self, path: Path, password: str) -> None:
        self._validate_password(password)

        with self._lock:
            storage = self._load()
        key: Optional[bytes] = None

        try:
            salt = os.urandom(32)
            key = self._derive_scrypt(password, salt)

            cipher = Fernet(key)
            encrypted = cipher.encrypt(
                json.dumps(storage, separators=(",", ":")).encode()
            )

            payload: ExportPayloadV2 = {
                "version": 2,
                "kdf": "scrypt",
                "kdf_params": {"n": 2**17, "r": 8, "p": 1},
                "salt": base64.b64encode(salt).decode(),
                "data": base64.b64encode(encrypted).decode(),
            }

            path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

            self._audit.log_credential_access("export", str(path))

        except Exception as exc:
            logger.exception("Export failed")
            raise SecurityError("Failed to export credentials") from exc

        finally:
            _wipe_bytes(key)

    def import_encrypted(self, path: Path, password: str) -> int:
        if not path.exists():
            raise ValidationError(f"File not found: {path}")

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            # Provide a clearer message for callers/tests when the file cannot
            # be parsed/read as JSON.
            raise ValidationError("Cannot read export file") from exc

        # Validate required export fields early so we return a clear
        # ValidationError when fields are missing instead of a generic
        # decryption error later on.
        if payload.get("version", 1) >= 2:
            if not payload.get("salt") or not payload.get("data"):
                raise ValidationError("Export missing required fields")

        salt = base64.b64decode(payload.get("salt", ""))
        encrypted = base64.b64decode(payload.get("data", ""))

        key: Optional[bytes] = None

        try:
            if payload.get("version", 1) >= 2:
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

    # =====================================================
    # Crypto helpers
    # =====================================================

    @staticmethod
    def _validate_password(password: str) -> None:
        if not password or len(password) < 12:
            raise ValidationError("Password must be at least 12 characters")

        classes = sum([
            any(c.isupper() for c in password),
            any(c.islower() for c in password),
            any(c.isdigit() for c in password),
            any(not c.isalnum() for c in password),
        ])

        if classes < 3:
            # Provide a friendly, testable message describing the requirement.
            raise ValidationError(
                "Password too weak: must include at least 3 of uppercase, lowercase, digits, or symbols"
            )

    @staticmethod
    def _derive_scrypt(password: str, salt: bytes) -> bytes:
        from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

        kdf = Scrypt(salt=salt, length=32, n=2**17, r=8, p=1)
        return base64.urlsafe_b64encode(kdf.derive(password.encode()))

    @staticmethod
    def _derive_pbkdf2(password: str, salt: bytes) -> bytes:
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=1_000_000,
        )
        return base64.urlsafe_b64encode(kdf.derive(password.encode()))

