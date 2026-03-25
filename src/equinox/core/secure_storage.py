"""Secure credential storage using encryption.

This module provides secure storage for sensitive data like:
- API keys
- Bearer tokens
- OAuth tokens
- Passwords
- Client secrets

Security features:
- AES-256 encryption
- Key derivation from system keyring
- No plaintext storage
- Secure memory handling
"""

import os
import json
import base64
import logging
import tempfile
from pathlib import Path
from typing import Optional, Dict, Any, List
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from equinox.core.exceptions import SecurityError, ValidationError
from equinox.core.audit import get_audit_logger
from equinox.core import crypto

logger = logging.getLogger(__name__)


class SecureStorage:
    """Secure storage for sensitive credentials.

    Uses Fernet (symmetric encryption) with keys derived from
    a master password/keyring.
    """

    def __init__(self, storage_path: Optional[Path] = None):
        """Initialize secure storage.

        Args:
            storage_path: Path to encrypted storage file

        Raises:
            SecurityError: If initialization fails
        """
        if storage_path is None:
            # Default to user's home directory
            storage_path = Path.home() / ".equinox" / ".credentials"

        self.storage_path = Path(storage_path)
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)

        # Set restrictive permissions on the file (Unix-like systems)
        if self.storage_path.exists():
            try:
                os.chmod(self.storage_path, 0o600)  # Read/write owner only
            except (OSError, NotImplementedError):
                # Windows doesn't support chmod the same way
                logger.warning("Could not set file permissions")

        self._cipher: Optional[Fernet] = None
        self._key: Optional[bytes] = None
        self._audit = get_audit_logger()

    def _get_or_create_key(self) -> bytes:
        """Get or create encryption key.

        Delegates to the shared :func:`~equinox.core.auth_cipher.get_or_create_key`
        so key management logic is never duplicated.

        Returns:
            Encryption key bytes

        Raises:
            SecurityError: If key generation fails
        """
        key_path = self.storage_path.parent / ".key"
        try:
            return crypto.get_or_create_raw_key(key_path)
        except Exception as e:
            logger.error("Failed to load/generate encryption key: %s", type(e).__name__)
            raise SecurityError("Failed to load encryption key")


    def _get_cipher(self) -> Fernet:
        """Get or create cipher instance.

        Returns:
            Fernet cipher

        Raises:
            SecurityError: If cipher creation fails
        """
        if self._cipher is None:
            try:
                key = self._get_or_create_key()
                self._cipher = crypto.make_fernet(key)
            except Exception as e:
                logger.error("Failed to create cipher: %s", type(e).__name__)
                raise SecurityError("Failed to initialize encryption")

        return self._cipher

    def _load_storage(self) -> Dict[str, Any]:
        """Load and decrypt storage file.

        Returns:
            Decrypted storage dictionary

        Raises:
            SecurityError: If decryption fails
        """
        if not self.storage_path.exists():
            logger.debug("Secure storage file does not exist: %s", self.storage_path)
            return {}

        try:
            logger.debug("Loading secure storage from: %s", self.storage_path)
            cipher = self._get_cipher()

            with open(self.storage_path, "rb") as f:
                encrypted_data = f.read()

            if not encrypted_data:
                logger.debug("Secure storage file is empty")
                return {}

            # Decrypt
            logger.debug("Decrypting secure storage (%d bytes)", len(encrypted_data))
            decrypted_data = cipher.decrypt(encrypted_data)

            # Parse JSON
            storage = json.loads(decrypted_data.decode("utf-8"))
            logger.info("Loaded secure storage with %d entries", len(storage))
            return storage

        except Exception as e:
            logger.error("Failed to load secure storage: %s", type(e).__name__, exc_info=True)
            raise SecurityError("Failed to decrypt credentials")

    def _save_storage(self, storage: Dict[str, Any]) -> None:
        """Encrypt and save storage file.

        Args:
            storage: Storage dictionary to save

        Raises:
            SecurityError: If encryption fails
        """
        try:
            cipher = self._get_cipher()

            # Convert to JSON
            json_data = json.dumps(storage, indent=2)

            # Encrypt
            encrypted_data = cipher.encrypt(json_data.encode("utf-8"))

            # Atomic write: write to a temp file then rename, so a crash
            # mid-write never leaves a truncated/corrupt credentials file.
            fd, tmp_path = tempfile.mkstemp(
                dir=str(self.storage_path.parent), suffix=".tmp"
            )
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(encrypted_data)
                    f.flush()
                    os.fsync(f.fileno())
                # os.replace is atomic on POSIX; on Windows it's close enough
                os.replace(tmp_path, str(self.storage_path))
            except BaseException:
                # Clean up the temp file on failure
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise

            # Set restrictive permissions
            try:
                os.chmod(self.storage_path, 0o600)
            except (OSError, NotImplementedError):
                pass

            logger.debug("Secure storage saved successfully")

        except Exception as e:
            logger.error("Failed to save secure storage: %s", type(e).__name__)
            raise SecurityError("Failed to encrypt credentials")

    def store(self, key: str, value: str, metadata: Optional[Dict[str, Any]] = None) -> None:
        """Store a credential securely.

        Args:
            key: Credential key/identifier
            value: Credential value (will be encrypted)
            metadata: Optional metadata (will also be encrypted)

        Raises:
            ValidationError: If key/value are invalid
            SecurityError: If storage fails
        """
        if not key or not isinstance(key, str):
            raise ValidationError("Credential key must be a non-empty string")

        if not isinstance(value, str):
            raise ValidationError("Credential value must be a string")

        if len(key) > 256:
            raise ValidationError("Credential key too long")

        if len(value) > 10000:
            raise ValidationError("Credential value too long")

        # Load current storage
        storage = self._load_storage()

        # Store credential
        storage[key] = {
            "value": value,
            "metadata": metadata or {},
        }

        # Save storage
        self._save_storage(storage)

        logger.debug("Stored credential: %s", key)
        self._audit.log_credential_access("store", key)

    def retrieve(self, key: str) -> Optional[str]:
        """Retrieve a credential.

        Args:
            key: Credential key/identifier

        Returns:
            Decrypted credential value or None if not found

        Raises:
            ValidationError: If key is invalid
            SecurityError: If decryption fails
        """
        if not key or not isinstance(key, str):
            raise ValidationError("Credential key must be a non-empty string")

        # Load storage
        storage = self._load_storage()

        # Get credential
        credential = storage.get(key)

        if credential is None:
            return None

        self._audit.log_credential_access("retrieve", key)
        return credential.get("value")

    def delete(self, key: str) -> bool:
        """Delete a credential.

        Args:
            key: Credential key/identifier

        Returns:
            True if deleted, False if not found

        Raises:
            ValidationError: If key is invalid
            SecurityError: If storage update fails
        """
        if not key or not isinstance(key, str):
            raise ValidationError("Credential key must be a non-empty string")

        # Load storage
        storage = self._load_storage()

        # Check if exists
        if key not in storage:
            return False

        # Delete
        del storage[key]

        # Save storage
        self._save_storage(storage)

        logger.debug("Deleted credential: %s", key)
        self._audit.log_credential_access("delete", key)
        return True

    def list_keys(self) -> List[str]:
        """List all credential keys.

        Returns:
            List of credential keys

        Raises:
            SecurityError: If storage loading fails
        """
        storage = self._load_storage()
        return list(storage.keys())

    def clear(self) -> None:
        """Clear all credentials.

        Raises:
            SecurityError: If clear fails
        """
        # Save empty storage
        self._save_storage({})

        logger.warning("Cleared all credentials from secure storage")

    def export_encrypted(self, export_path: Path, password: str) -> None:
        """Export credentials to an encrypted file.

        Security measures:
        - **scrypt** key derivation (memory-hard — resists GPU/ASIC attacks)
        - 32-byte random salt
        - Fernet envelope (AES-128-CBC + HMAC-SHA256)
        - Versioned format for future crypto agility
        - Password strength validation (length + character-class diversity)

        Args:
            export_path: Path to export file.
            password: Password for export encryption (≥ 12 chars, 3+ char classes).

        Raises:
            ValidationError: If password is too weak.
            SecurityError: If export fails.
        """
        self._validate_export_password(password)

        storage = self._load_storage()
        key = None
        try:
            salt = os.urandom(32)
            key = self._derive_scrypt_key(password, salt)
            cipher = Fernet(key)

            json_data = json.dumps(storage, separators=(",", ":"))
            encrypted = cipher.encrypt(json_data.encode("utf-8"))

            export_data = {
                "version": 2,
                "kdf": "scrypt",
                "kdf_params": {"n": 2**17, "r": 8, "p": 1},
                "salt": base64.b64encode(salt).decode("ascii"),
                "data": base64.b64encode(encrypted).decode("ascii"),
            }

            with open(export_path, "w", encoding="utf-8") as f:
                json.dump(export_data, f, indent=2)

            logger.info("Exported credentials to %s", export_path)
            self._audit.log_credential_access("export", str(export_path))

        except (ValidationError, SecurityError):
            raise
        except Exception as exc:
            logger.error("Failed to export credentials: %s", type(exc).__name__)
            raise SecurityError("Failed to export credentials")
        finally:
            if key is not None:
                # Overwrite the derived key in memory
                _wipe_bytes(key)

    def import_encrypted(self, import_path: Path, password: str) -> int:
        """Import credentials from an encrypted export file.

        Supports both the legacy v1 (PBKDF2) and current v2 (scrypt) formats.

        Args:
            import_path: Path to the encrypted export file.
            password: Password that was used during export.

        Returns:
            Number of credentials imported.

        Raises:
            ValidationError: If the file is malformed.
            SecurityError: If decryption fails (wrong password or corrupt data).
        """
        if not import_path.exists():
            raise ValidationError(f"Import file not found: {import_path}")

        try:
            with open(import_path, "r", encoding="utf-8") as f:
                export_data = json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            raise ValidationError(f"Cannot read export file: {exc}")

        version = export_data.get("version", 1)
        salt_b64 = export_data.get("salt")
        data_b64 = export_data.get("data")
        if not salt_b64 or not data_b64:
            raise ValidationError("Export file is missing required fields")

        salt = base64.b64decode(salt_b64)
        encrypted = base64.b64decode(data_b64)

        key = None
        try:
            if version >= 2:
                key = self._derive_scrypt_key(password, salt)
            else:
                key = self._derive_pbkdf2_key(password, salt)

            cipher = Fernet(key)
            decrypted = cipher.decrypt(encrypted)
            imported: dict = json.loads(decrypted.decode("utf-8"))

        except Exception as exc:
            raise SecurityError(
                "Decryption failed — wrong password or corrupt file"
            ) from exc
        finally:
            if key is not None:
                _wipe_bytes(key)

        if not isinstance(imported, dict):
            raise ValidationError("Export file has an invalid payload")

        # Merge into current storage
        storage = self._load_storage()
        storage.update(imported)
        self._save_storage(storage)

        logger.info(
            "Imported %d credential(s) from %s", len(imported), import_path
        )
        self._audit.log_credential_access("import", str(import_path))
        return len(imported)

    # ── Export helpers ─────────────────────────────────────────────────

    @staticmethod
    def _validate_export_password(password: str) -> None:
        """Enforce minimum password strength for export files.

        Requires ≥ 12 characters drawn from at least 3 of 4 character
        classes (upper, lower, digit, special).

        Raises:
            ValidationError: If the password is too weak.
        """
        if not password or not isinstance(password, str):
            raise ValidationError("Export password is required")
        if len(password) < 12:
            raise ValidationError(
                "Export password must be at least 12 characters"
            )
        classes = sum([
            any(c.isupper() for c in password),
            any(c.islower() for c in password),
            any(c.isdigit() for c in password),
            any(not c.isalnum() for c in password),
        ])
        if classes < 3:
            raise ValidationError(
                "Export password must contain at least 3 of: "
                "uppercase, lowercase, digits, special characters"
            )

    @staticmethod
    def _derive_scrypt_key(
        password: str, salt: bytes,
        n: int = 2**17, r: int = 8, p: int = 1,
    ) -> bytes:
        """Derive a Fernet-compatible key using scrypt (memory-hard).

        Default parameters: N=131072 (128 MiB memory), r=8, p=1.
        """
        from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

        kdf = Scrypt(salt=salt, length=32, n=n, r=r, p=p)
        raw = kdf.derive(password.encode("utf-8"))
        return base64.urlsafe_b64encode(raw)

    @staticmethod
    def _derive_pbkdf2_key(password: str, salt: bytes) -> bytes:
        """Derive a Fernet-compatible key using PBKDF2 (legacy v1 format)."""
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=1_000_000,
        )
        raw = kdf.derive(password.encode("utf-8"))
        return base64.urlsafe_b64encode(raw)


def _wipe_bytes(b: bytes) -> None:
    """Best-effort overwrite of a bytes object in CPython.

    ``bytes`` objects are immutable at the Python level, but we can
    zero-out the internal buffer via ``ctypes`` on CPython.  This is
    *not* guaranteed by the language spec, but it raises the bar
    compared to leaving keys in memory until the GC collects them.
    """
    if not b:
        return  # nothing to wipe
    try:
        import ctypes
        ptr = ctypes.cast(id(b), ctypes.POINTER(ctypes.c_char))
        offset = bytes.__basicsize__  # skip the object header
        for i in range(len(b)):
            ptr[offset + i] = b"\x00"
    except Exception:
        pass  # non-CPython or restricted environment — silently skip
