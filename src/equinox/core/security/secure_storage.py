"""Secure credential storage moved from core.secure_storage.py"""

from __future__ import annotations

import logging
import json
import base64
import threading
import time
import sqlite3
from pathlib import Path
from typing import Optional, Dict, Any

from cryptography.fernet import Fernet

from equinox.core.exceptions import SecurityError, ValidationError
from equinox.core.audit import get_audit_logger
from equinox.core.security import crypto as _crypto
from equinox.core.redact import redact_body

logger = logging.getLogger(__name__)
_audit = get_audit_logger()

# The export of this module aligns with the original secure storage surface.
class SecureStorage:
    def __init__(self, storage_path: Optional[Path] = None):
        self.storage_path = Path(storage_path or Path.home() / ".equinox" / ".credentials")
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        if self.storage_path.exists():
            self.storage_path.chmod(0o600)
        self._cipher: Optional[Fernet] = None
        self._audit = _audit
        self._lock = threading.Lock()

    def _get_key(self) -> bytes:
        key_path = (self.storage_path.parent / ".key") if False else _crypto.default_key_path()
        # Prefer OS-based key if available via core.security.crypto path
        return _crypto.get_or_create_raw_key(key_path)

    def _get_cipher(self) -> Fernet:
        if self._cipher is None:
            self._cipher = _crypto.make_fernet(self._get_key())
        return self._cipher

    def _load(self) -> Dict[str, Dict[str, Any]]:
        if not self.storage_path.exists():
            return {}
        cipher = self._get_cipher()
        data = self.storage_path.read_bytes()
        if not data:
            return {}
        decrypted = cipher.decrypt(data)
        return json.loads(decrypted.decode("utf-8"))

    def _save(self, storage: Dict[str, Dict[str, Any]]):
        cipher = self._get_cipher()
        payload = json.dumps(storage, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        encrypted = cipher.encrypt(payload)
        Path(self.storage_path).write_bytes(encrypted)
        self.storage_path.chmod(0o600)

    def store(self, key: str, value: str, metadata: Optional[Dict[str, Any]] = None) -> None:
        self._lock.acquire()
        try:
            self._validate_key_value(key, value)
            storage = self._load()
            storage[key] = {"value": value, "metadata": metadata or {}}
            self._save(storage)
        finally:
            self._lock.release()

    def retrieve(self, key: str) -> Optional[str]:
        self._lock.acquire()
        try:
            self._validate_key_value(key)
            storage = self._load()
            ent = storage.get(key)
            return ent.get("value") if ent else None
        finally:
            self._lock.release()

    def delete(self, key: str) -> bool:
        self._lock.acquire()
        try:
            self._validate_key_value(key)
            storage = self._load()
            if key not in storage:
                return False
            del storage[key]
            self._save(storage)
            return True
        finally:
            self._lock.release()

    def list_keys(self) -> list[str]:
        self._lock.acquire()
        try:
            storage = self._load()
            return list(storage.keys())
        finally:
            self._lock.release()

    def clear(self) -> None:
        self._lock.acquire()
        try:
            self._save({})
        finally:
            self._lock.release()

    # Simple validators (kept minimal for compatibility)
    @staticmethod
    def _validate_key_value(key: str, value: Optional[str] = None) -> None:
        if not key or not isinstance(key, str):
            raise ValidationError("Credential key must be a non-empty string")
        if len(key) > 256:
            raise ValidationError("Credential key too long")
        if value is not None and not isinstance(value, str):
            raise ValidationError("Credential value must be a string")
