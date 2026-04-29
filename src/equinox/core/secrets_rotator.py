"""Utility to rotate plaintext secrets to a password-based encryption.

This module provides a small, targeted facility to re-encrypt plaintext
secret fields using a new master password. It does not attempt to decrypt already
encrypted values (prefix enc:) to avoid cross-key decrypt issues.
"""
from __future__ import annotations

import sqlite3
from typing import Optional

from equinox.core.secrets_password import rotate_all_secrets as _rotate_all_secrets

_LOGGER_TAG = "secrets_rotator"


def rotate_all_secrets(db_path: str, new_password: Optional[str] = None) -> None:
    """Rotate plaintext secrets in the database to be encrypted with *new_password*.

    This is a targeted rotation: plaintext values will be wrapped with enc:...
    and not yet-encrypted values will be encrypted using the derived Fernet key.
    """
    # Reuse the existing rotation logic from the shared helper
    _rotate_all_secrets(db_path, new_password=new_password)
