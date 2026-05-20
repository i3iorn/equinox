"""Global master-password-based secret encryption utilities.

This module provides a password-based key derivation for encrypting secrets
at rest, replacing the previous file-based Fernet key approach when a master
password is configured. It also provides a lightweight rotation helper to
re-encrypt plaintext secrets with a new master password.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
from getpass import getpass
from pathlib import Path
from typing import Callable, cast

from cryptography.fernet import Fernet

from equinox.core.config.flags import is_strict_secret_rotation_enabled

_SALT_FILE = Path.home() / ".equinox" / "salt.bin"
_MASTER_PW_ENV = "EQUINOX_MASTER_PASSWORD"

# In-memory cache for performance and to preserve password during runtime
_cached_password: str | None = None
_cached_fernet: Fernet | None = None
_password_prompt_callback: Callable[[], str | None] | None = None

_ENC_PREFIX = "enc:"

logger = logging.getLogger(__name__)


def _read_or_create_salt() -> bytes:
    """Return the salt used for deriving the key; create it if missing."""
    _salt_dir = _SALT_FILE.parent
    _salt_dir.mkdir(parents=True, exist_ok=True)
    if _SALT_FILE.exists():
        return _SALT_FILE.read_bytes()
    # 16-byte salt is ample for PBKDF2
    import secrets as _secrets

    s = _secrets.token_bytes(16)
    _SALT_FILE.write_bytes(s)
    return s


def _derive_key_from_password(password: str, salt: bytes) -> bytes:
    """Derive a 32-byte key suitable for Fernet from a password and salt."""
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100000, dklen=32)
    return base64.urlsafe_b64encode(dk)


def get_master_password() -> str | None:
    """Return the configured master password, prompting if necessary."""
    global _cached_password
    if _cached_password is not None:
        return _cached_password
    # First, an environment variable can supply the password (non-interactive setup)
    pw = os.environ.get(_MASTER_PW_ENV)
    if pw:
        _cached_password = pw
        return pw
    # GUI can register a secure prompt callback to avoid terminal prompts.
    callback = cast(Callable[[], str | None] | None, _password_prompt_callback)
    if callback is not None:
        try:
            pw = callback()
        except Exception:
            pw = None
        if pw:
            _cached_password = pw
            return pw
        # If a callback is explicitly set (e.g., GUI), do not fall back to
        # terminal getpass so the app never blocks on hidden CLI input.
        return None

    # Otherwise, prompt the user interactively in terminal contexts.
    try:
        pw = getpass("Enter master password for encrypted secrets: ")
    except Exception:
        pw = None
    if pw:
        _cached_password = pw
        return pw
    return None


def set_master_password(password: str) -> None:
    """Explicitly set the master password for the current process."""
    global _cached_password, _cached_fernet
    _cached_password = password
    _cached_fernet = None


def set_master_password_prompt(callback: Callable[[], str | None] | None) -> None:
    """Set or clear the runtime password prompt callback.

    GUI startup should register a callback that opens a modal dialog.
    Passing ``None`` clears any existing callback.
    """
    global _password_prompt_callback
    _password_prompt_callback = callback


def is_master_password_configured() -> bool:
    return get_master_password() is not None


def _get_salt() -> bytes:
    return _read_or_create_salt()


def _derive_fernet_from_password(password: str | None) -> Fernet | None:
    if not password:
        return None
    salt = _get_salt()
    key = _derive_key_from_password(password, salt)
    return Fernet(key)


def get_fernet_for_password(password: str | None = None) -> Fernet | None:
    """Return a Fernet instance derived from the given password.

    If password is None, attempts to load a configured master password. If no
    master password is configured, returns None.
    """
    global _cached_fernet
    if _cached_fernet is not None:
        return _cached_fernet
    if password is None:
        password = get_master_password()
    f = _derive_fernet_from_password(password)
    _cached_fernet = f
    return f


def ensure_master_password_initialized() -> Fernet | None:
    """Factory helper to ensure a Fernet instance exists from master password.

    Startup bootstrap rotates plaintext secrets into enc: blobs automatically
    if EQUINOX_DB_PATH is provided, using the discovered master password.
    """
    f = get_fernet_for_password()
    # Startup safeguard: if a DB path is provided, rotate plaintext secrets
    # so they are encrypted under the current master password.
    if f is not None:
        try:
            db_path = os.environ.get("EQUINOX_DB_PATH")
            if db_path:
                rotate_all_secrets(db_path, new_password=get_master_password())
        except Exception as exc:
            logger.error(
                "secret_rotation_startup_failed op=ensure_master_password_initialized db_path=%r",
                os.environ.get("EQUINOX_DB_PATH"),
                exc_info=True,
            )
            if is_strict_secret_rotation_enabled():
                raise RuntimeError(
                    "Startup secret rotation failed while strict mode is enabled"
                ) from exc
    return f


def rotate_all_secrets(db_path: str, new_password: str | None = None) -> None:
    """Rotate all plaintext secrets to be encrypted with a new master password.

    This function will take all known plaintext secret fields and re-encrypt
    them using the Fernet derived from *new_password*. If the provided value
    already starts with the encryption prefix (enc:), it will be skipped.

    Tables touched (best-effort):
      - oauth_clients.client_secret
      - saved_credentials.config
      - collections.auth_data (and auth_type)

    Note: This only encrypts plaintext values; previously encrypted values using an
    older master password are not decrypted (to avoid attempting to derive the
    old key without access to it). They will be left as-is until a separate
    decryption/rotation path is provided.
    """
    fernet = get_fernet_for_password(new_password)
    if not fernet:
        # No master password configured; nothing to rotate
        return

    import sqlite3

    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("BEGIN")

        def enc(value: str | None) -> str | None:
            if value is None:
                return None
            if isinstance(value, str) and value.startswith(_ENC_PREFIX):
                return value
            token = fernet.encrypt(value.encode("utf-8"))
            return _ENC_PREFIX + token.decode("ascii")

        # Rotate oauth_clients.client_secret
        for row in conn.execute("SELECT id, client_secret FROM oauth_clients"):
            uid, secret = row[0], row[1]
            if secret is None:
                continue
            new_secret = enc(secret)
            if new_secret != secret:
                conn.execute(
                    "UPDATE oauth_clients SET client_secret=? WHERE id=?", (new_secret, uid)
                )

        # Rotate saved_credentials.config
        for row in conn.execute("SELECT id, config FROM saved_credentials"):
            sid, cfg = row[0], row[1]
            if cfg is None:
                continue
            new_cfg = enc(cfg)
            if new_cfg != cfg:
                conn.execute("UPDATE saved_credentials SET config=? WHERE id=?", (new_cfg, sid))

        # Rotate collection-level auth (auth_type + auth_data)
        for row in conn.execute("SELECT id, auth_type, auth_data FROM collections"):
            cid, a_type, a_data = row[0], row[1], row[2]
            if not a_type or a_data is None:
                continue
            new_data = enc(a_data)
            if new_data != a_data:
                conn.execute("UPDATE collections SET auth_data=? WHERE id=?", (new_data, cid))

        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")  # type: ignore
        except Exception:
            pass
        raise
    finally:
        try:
            conn.close()
        except Exception:
            pass
