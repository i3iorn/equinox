"""Global master-password-based secret encryption utilities.

This module provides a password-based key derivation for encrypting secrets
at rest, replacing the previous file-based Fernet key approach when a master
password is configured. It also provides a lightweight rotation helper to
re-encrypt plaintext secrets with a new master password.

In-memory limitation: the master password is held in an immutable Python
``str`` (``_cached_password``) and the derived Fernet stays cached for the
process lifetime. Python strings cannot be zeroized, so the password cannot
be wiped from memory — this is a documented property of the design, not an
oversight. Prefer supplying the password via the GUI prompt callback or a
short-lived environment variable over persistent configuration.

Environment exposure: when ``EQUINOX_MASTER_PASSWORD`` is used, the password
is readable via ``/proc/<pid>/environ``, crash dumps, and shell history.
A WARNING is logged so the trade-off is visible in production logs
(issue #71).
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
from collections.abc import Callable
from getpass import getpass
from pathlib import Path
from sqlite3 import Connection

from cryptography.fernet import Fernet
from equinox.core.config.flags import is_strict_secret_rotation_enabled
from equinox.core.exceptions import SecurityError
from equinox.security.auth_cipher import reset_fernet_cache

_SALT_FILE = Path.home() / ".equinox" / "salt.bin"
_SALT_BYTES = 16
_MASTER_PW_ENV = "EQUINOX_MASTER_PASSWORD"

# In-memory cache for performance and to preserve password during runtime
_cached_password: str | None = None
_cached_fernet: Fernet | None = None
_password_prompt_callback: Callable[[], str | None] | None = None

_ENC_PREFIX = "enc:"

logger = logging.getLogger(__name__)


def _read_or_create_salt() -> bytes:
    """Return the salt used for deriving the key; create it if missing.

    The salt is not secret, but the file is created ``0o600`` like the key
    file so other users on a shared host cannot read or replace it. The salt
    is not recoverable — losing it makes every master-password blob
    undecryptable — so creation uses ``O_EXCL`` to avoid a concurrent clobber.
    """
    _salt_dir = _SALT_FILE.parent
    _salt_dir.mkdir(parents=True, exist_ok=True)
    if _SALT_FILE.exists():
        salt = _SALT_FILE.read_bytes()
        if len(salt) != _SALT_BYTES:
            # Fail loud: a truncated or tampered salt silently changes the
            # derived key, which would only surface as undecryptable
            # ciphertext later (issue #71). Regenerating here would be wrong
            # whenever encrypted data already exists under the real salt.
            raise SecurityError(
                f"{_SALT_FILE} is corrupt: expected {_SALT_BYTES} bytes, "
                f"found {len(salt)}. If it was truncated or tampered with, "
                "restore it from backup — regenerating it makes existing "
                "encrypted secrets undecryptable.",
                details={"salt_bytes": len(salt)},
            )
        return salt
    # 16-byte salt is ample for PBKDF2
    import secrets as _secrets

    s = _secrets.token_bytes(16)
    try:
        fd = os.open(str(_SALT_FILE), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        # Another process created the salt first; use that one.
        return _SALT_FILE.read_bytes()
    with os.fdopen(fd, "wb") as fh:
        fh.write(s)
    try:
        os.chmod(_SALT_FILE, 0o600)
    except (OSError, NotImplementedError):
        logger.warning("Could not set restrictive permissions on %s", _SALT_FILE)
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
        # Env vars leak via /proc/<pid>/environ, crash dumps, and shell
        # history; warn so the trade-off is visible (issue #71).
        logger.warning(
            "Using master password from %s environment variable; prefer the "
            "GUI prompt callback — environment variables are exposed via "
            "/proc/<pid>/environ, crash dumps, and shell history",
            _MASTER_PW_ENV,
        )
        _cached_password = pw
        return pw
    # GUI can register a secure prompt callback to avoid terminal prompts.
    callback = _password_prompt_callback
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
    """Explicitly set the master password for the current process.

    Resets the cipher cache in ``security.auth_cipher`` so the new password
    takes effect immediately — otherwise a cached master-password Fernet
    ("always wins" in ``get_fernet``) keeps new secrets encrypted under the
    old password until the process restarts.
    """
    global _cached_password, _cached_fernet
    _cached_password = password
    _cached_fernet = None
    reset_fernet_cache()


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

    An explicit *password* always wins. Startup caches a Fernet for the
    configured master password; a later rotation passes the *new* password
    here, and returning the cached instance silently re-encrypted under the
    old key. Only fall back to the cache when no password was supplied.

    If *password* is None, reuse the cached instance or derive from the
    configured master password. If no master password is configured, returns
    None.
    """
    global _cached_fernet
    if password is not None:
        _cached_fernet = _derive_fernet_from_password(password)
        return _cached_fernet
    if _cached_fernet is not None:
        return _cached_fernet
    _cached_fernet = _derive_fernet_from_password(get_master_password())
    return _cached_fernet


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
                    "Startup secret rotation failed while strict mode is enabled",
                ) from exc
    return f


def rotate_all_secrets(db_path: str, new_password: str | None = None) -> None:
    """Rotate all plaintext secrets to be encrypted with a new master password."""
    fernet = get_fernet_for_password(new_password)
    if not fernet:
        return

    conn = _open_sqlite_connection(db_path)
    try:
        _begin_transaction(conn)
        encrypt = _build_encrypt_fn(fernet)

        _rotate_oauth_client_secrets(conn, encrypt)
        _rotate_saved_credentials(conn, encrypt)
        _rotate_collection_auth(conn, encrypt)

        _commit_transaction(conn)
    except Exception:
        _rollback_safely(conn)
        raise
    finally:
        _close_safely(conn)


def _open_sqlite_connection(db_path: str) -> Connection:
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _begin_transaction(conn: Connection) -> None:
    conn.execute("BEGIN")


def _commit_transaction(conn: Connection) -> None:
    conn.execute("COMMIT")


def _rollback_safely(conn: Connection) -> None:
    try:
        conn.execute("ROLLBACK")
    except Exception:
        pass


def _close_safely(conn: Connection) -> None:
    try:
        conn.close()
    except Exception:
        pass


def _build_encrypt_fn(fernet: Fernet) -> Callable[[str | None], str | None]:
    def encrypt(value: str | None) -> str | None:
        if value is None:
            return None
        if isinstance(value, str) and value.startswith(_ENC_PREFIX):
            return value

        token = fernet.encrypt(value.encode("utf-8"))
        if not isinstance(token, bytes):
            raise TypeError("Fernet.encrypt returned non-bytes token")

        return _ENC_PREFIX + token.decode("ascii")

    return encrypt


def _rotate_oauth_client_secrets(
    conn: Connection,
    encrypt: Callable[[str | None], str | None],
) -> None:
    for row in conn.execute("SELECT id, client_secret FROM oauth_clients"):
        uid, secret = row["id"], row["client_secret"]
        if secret is None:
            continue

        new_secret = encrypt(secret)
        if new_secret != secret:
            conn.execute(
                "UPDATE oauth_clients SET client_secret=? WHERE id=?",
                (new_secret, uid),
            )


def _rotate_saved_credentials(
    conn: Connection,
    encrypt: Callable[[str | None], str | None],
) -> None:
    for row in conn.execute("SELECT id, config FROM saved_credentials"):
        sid, cfg = row["id"], row["config"]
        if cfg is None:
            continue

        new_cfg = encrypt(cfg)
        if new_cfg != cfg:
            conn.execute(
                "UPDATE saved_credentials SET config=? WHERE id=?",
                (new_cfg, sid),
            )


def _rotate_collection_auth(conn: Connection, encrypt: Callable[[str | None], str | None]) -> None:
    for row in conn.execute("SELECT id, auth_type, auth_data FROM collections"):
        cid, a_type, a_data = row["id"], row["auth_type"], row["auth_data"]
        if not a_type or a_data is None:
            continue

        new_data = encrypt(a_data)
        if new_data != a_data:
            conn.execute(
                "UPDATE collections SET auth_data=? WHERE id=?",
                (new_data, cid),
            )
