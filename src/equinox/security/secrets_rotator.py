"""Rotation helper moved from core.secrets_rotator.py to security."""

from __future__ import annotations

from typing import Optional

from equinox.security.secrets_password import rotate_all_secrets as _rotate_all_secrets


def rotate_all_secrets(db_path: str, new_password: Optional[str] = None) -> None:
    """Rotate plaintext secrets in the database to be encrypted with *new_password*."""
    _rotate_all_secrets(db_path, new_password=new_password)
