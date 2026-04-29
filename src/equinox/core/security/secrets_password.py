"""Global master-password-based secret encryption utilities.

This module provides a password-based key derivation for encrypting secrets
at rest, replacing the previous file-based Fernet key approach when a master
password is configured. It also provides a lightweight rotation helper to
re-encrypt plaintext secrets with a new master password.
"""

from __future__ import annotations

import base64
import hashlib
import os
import json
from pathlib import Path
from typing import Optional
from getpass import getpass
from cryptography.fernet import Fernet

_SALT_FILE = Path.home() / ".equinox" / "salt.bin"
_MASTER_PW_ENV = "EQUINOX_MASTER_PASSWORD"

_cached_password: Optional[str] = None
_cached_fernet: Optional[Fernet] = None

_ENC_PREFIX = "enc:"


def _read_or_create_salt() -> bytes:
    _salt_dir = _SALT_FILE.parent
    _salt_dir.mkdir(parents=True, exist_ok=True)
    if _SALT_FILE.exists():
        return _SALT_FILE.read_bytes()
    import secrets as _secrets
    s = _secrets.token_bytes(16)
    _SALT_FILE.write_bytes(s)
    return s


def _derive_key_from_password(password: str, salt: bytes) -> bytes:
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100000, dklen=32)
    return base64.urlsafe_b64encode(dk)


def get_master_password() -> Optional[str]:
    global _cached_password
    if _cached_password is not None:
        return _cached_password
    pw = os.environ.get(_MASTER_PW_ENV)
    if pw:
        _cached_password = pw
        return pw
    try:
        pw = getpass("Enter master password for encrypted secrets: ")
    except Exception:
        pw = None
    if pw:
        _cached_password = pw
        return pw
    return None


def set_master_password(password: str) -> None:
    global _cached_password, _cached_fernet
    _cached_password = password
    _cached_fernet = None


def is_master_password_configured() -> bool:
    return get_master_password() is not None


def _get_salt() -> bytes:
    return _read_or_create_salt()


def _derive_fernet_from_password(password: Optional[str]) -> Optional[Fernet]:
    if not password:
        return None
    salt = _get_salt()
    key = _derive_key_from_password(password, salt)
    return Fernet(key)


def get_fernet_for_password(password: Optional[str] = None) -> Optional[Fernet]:
    global _cached_fernet
    if _cached_fernet is not None:
        return _cached_fernet
    if password is None:
        password = get_master_password()
    f = _derive_fernet_from_password(password)
    _cached_fernet = f
    return f


def ensure_master_password_initialized() -> Optional[Fernet]:
    f = get_fernet_for_password()
    if f is not None:
        try:
            db_path = os.environ.get("EQUINOX_DB_PATH")
            if db_path:
                from equinox.core.secrets_password import rotate_all_secrets  # lazy import
                rotate_all_secrets(db_path, new_password=get_master_password())
        except Exception:
            pass
        
    return f


def rotate_all_secrets(db_path: str, new_password: Optional[str] = None) -> None:
    fernet = get_fernet_for_password(new_password)
    if not fernet:
        return
    import sqlite3
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("BEGIN")
        def enc(value: Optional[str]) -> Optional[str]:
            if value is None:
                return None
            if isinstance(value, str) and value.startswith(_ENC_PREFIX):
                return value
            token = fernet.encrypt(value.encode("utf-8"))
            return _ENC_PREFIX + token.decode("ascii")
        for row in conn.execute("SELECT id, client_secret FROM oauth_clients"):
            uid, secret = row[0], row[1]
            if secret is None:
                continue
            new_secret = enc(secret)
            if new_secret != secret:
                conn.execute("UPDATE oauth_clients SET client_secret=? WHERE id=?", (new_secret, uid))
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        try:
            conn.close()
        except Exception:
            pass
