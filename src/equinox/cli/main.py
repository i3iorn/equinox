"""Lightweight CLI for secret rotation and quick ops.

This module provides a minimal CLI surface for managing secret rotation
without launching the GUI. It is intentionally small and safe to import in
environments that do not have PyQt dependencies.
"""
from __future__ import annotations

import os
from typing import Optional
import click
from getpass import getpass

from equinox.core.security.secrets_password import rotate_all_secrets


@click.group()
def main():
    """Equinox command-line helpers."""
    pass


@main.command()
@click.option("--db-path", "db_path", envvar="EQUINOX_DB_PATH", help="Path to the Equinox SQLite DB.")
@click.option("--new-password", "new_password", help="New master password to use for encryption (hidden).")
def rotate_secrets(db_path: str, new_password: Optional[str]) -> None:
    """Rotate plaintext secrets to be encrypted with a master password.

    If --db-path is not provided, the EQUINOX_DB_PATH environment variable will be used.
    If --new-password is not provided, the command will prompt for it securely.
    """
    if not db_path:
        click.echo("Error: --db-path is required (or EQUINOX_DB_PATH must be set).", err=True)
        raise SystemExit(2)
    if not new_password:
        new_password = getpass("Enter new master password for rotation: ")
        if not new_password:
            click.echo("No password provided; aborting.", err=True)
            raise SystemExit(1)
    rotate_all_secrets(db_path, new_password=new_password)
    click.echo("Secret rotation completed.")


def main_entry():
    main()
