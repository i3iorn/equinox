"""Lightweight CLI for secret rotation and quick ops.

This module provides a minimal CLI surface for managing secret rotation,
GUI launching, and other operations without requiring all dependencies upfront.
It is intentionally designed so that PyQt is not required for CLI-only operations.
"""

# mypy: disable-error-code=untyped-decorator

from __future__ import annotations

import click

from equinox.security.secrets_password import rotate_all_secrets


@click.group()
def main() -> None:
    """Equinox command-line helpers."""
    pass


@main.command()
def gui() -> None:
    """Launch the Equinox GUI application.

    Starts the interactive PyQt6 GUI for managing API requests, collections,
    and environments. PyQt6 is imported only when this command is invoked.
    """
    from equinox.gui.app import main as gui_main

    gui_main()


@main.command()
@click.option(
    "--db-path",
    "db_path",
    envvar="EQUINOX_DB_PATH",
    help="Path to the Equinox SQLite DB.",
)
@click.option(
    "--new-password",
    "new_password",
    help="New master password to use for encryption.",
    prompt="Enter new master password for rotation",
    hide_input=True,
    confirmation_prompt=True,
)
def rotate_secrets(db_path: str, new_password: str | None) -> None:
    """Rotate plaintext secrets to be encrypted with a master password.

    If --db-path is not provided, the EQUINOX_DB_PATH environment variable will be used.
    If --new-password is not provided, the command will prompt for it securely.
    """
    if not db_path:
        click.echo("Error: --db-path is required (or EQUINOX_DB_PATH must be set).", err=True)
        raise SystemExit(2)
    if not new_password:
        click.echo("No password provided; aborting.", err=True)
        raise SystemExit(1)
    rotate_all_secrets(db_path, new_password=new_password)
    click.echo("Secret rotation completed.")


def main_entry() -> None:
    """Entry point for the CLI application."""
    main()
