"""Utility to rotate plaintext secrets to a password-based encryption.

This module provides a small, targeted facility to re-encrypt plaintext
secret fields using a new master password. It does not attempt to decrypt already
encrypted values (prefix enc:) to avoid cross-key decrypt issues.
"""
from __future__ import annotations

from typing import Optional

from equinox.core.security.secrets_rotator import rotate_all_secrets as _rot  # type: ignore


def rotate_all_secrets(db_path: str, new_password: Optional[str] = None) -> None:
    _rot(db_path, new_password)
