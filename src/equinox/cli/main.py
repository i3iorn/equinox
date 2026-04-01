"""Main CLI entry point.

Defines the top-level ``cli`` group, shared helpers (``get_db``,
``get_interpolation_variables``), and registers every sub-module.
"""

import logging
import os
import sys
from pathlib import Path

import click
from dotenv import load_dotenv

from equinox import __version__
from equinox.storage import Database, EnvironmentManager, get_db as _storage_get_db

logger = logging.getLogger(__name__)


# ── Shared helpers (imported by sub-modules) ─────────────────────────────────


def get_db() -> Database:
    """Get database instance (delegates to shared storage factory)."""
    logger.debug("Retrieving database instance")
    return _storage_get_db()


def load_environment_variables(env_file: Path = None):
    """Load environment variables from a ``.env`` file.

    Args:
        env_file: Path to .env file (default: .env in current directory)
    """
    if env_file is None:
        env_file = Path.cwd() / ".env"
    if env_file.exists():
        try:
            logger.debug("Loading environment variables from %s", env_file)
            load_dotenv(env_file, override=False)
            logger.info("Environment variables loaded from %s", env_file)
        except Exception as e:
            logger.warning("Failed to load environment file %s: %s", env_file, e)
            click.echo(f"Warning: Failed to load {env_file}: {e}", err=True)
    else:
        logger.debug("Environment file not found at %s, skipping", env_file)


def get_interpolation_variables(db: Database, collection_id: int = None) -> dict:
    """Collect all available interpolation variables.

    Sources (in precedence order, later overrides earlier):
    1. Active DB environment variables
    2. Inherited collection variables (groups + collection-specific)
    3. ``EQUINOX_*`` environment variables (and other valid OS env vars)
    """
    import re
    variables = {}
    logger.debug("Collecting interpolation variables (collection_id=%s)", collection_id)

    try:
        env_manager = EnvironmentManager(db)
        active_env = env_manager.get_active_environment()
        if active_env and isinstance(active_env.get("variables"), dict):
            variables.update(active_env["variables"])
            logger.debug("Added %d active environment variables", len(active_env["variables"]))
    except Exception as exc:
        logger.debug("Could not load active environment variables: %s", exc)
        click.echo(f"Could not load environment variables: {exc}", err=True)

    if collection_id is not None:
        try:
            from equinox.storage import CollectionManager
            collection_manager = CollectionManager(db)
            collection_vars = collection_manager.get_all_collection_variables(collection_id)
            variables.update(collection_vars)
            logger.debug("Added %d collection variables for collection %d", len(collection_vars), collection_id)
        except Exception as exc:
            logger.debug("Could not load collection variables for collection %d: %s", collection_id, exc)
            click.echo(f"Could not load collection variables for {collection_id}: {exc}", err=True)

    # Only include EQUINOX_* vars, and filter other OS env vars to valid names
    # (Windows has vars like PROGRAMFILES(X86) which are invalid for interpolation)
    valid_var_pattern = re.compile(r'^[a-zA-Z0-9_-]+$')
    os_vars_added = 0
    for k, v in os.environ.items():
        if k.startswith("EQUINOX_") or valid_var_pattern.match(k):
            variables[k] = v
            os_vars_added += 1

    logger.debug("Added %d OS environment variables (valid names only)", os_vars_added)
    logger.debug("Total interpolation variables collected: %d", len(variables))
    return variables


# ── Top-level CLI group ──────────────────────────────────────────────────────


@click.group()
@click.version_option(version=__version__)
@click.option("--debug", is_flag=True, help="Enable debug mode")
@click.option("--env-file", type=click.Path(exists=False),
              help="Path to .env file for variable interpolation")
@click.pass_context
def cli(ctx, debug, env_file):
    """Equinox - A local-first API testing tool"""
    ctx.ensure_object(dict)
    ctx.obj["DEBUG"] = debug

    # Initialise structured logging for the CLI — same JSON log file as the
    # GUI, plus human-readable stderr output.  --debug lowers the console
    # level so users see verbose output without needing env vars.
    from equinox.core.log_setup import configure_logging
    console_level = logging.DEBUG if debug else logging.WARNING
    configure_logging(console_level=console_level)

    if debug:
        logger.debug("Debug mode enabled")
    logger.debug("Equinox CLI started (version=%s)", __version__)
    if env_file:
        logger.debug("Loading environment file: %s", env_file)
    load_environment_variables(Path(env_file) if env_file else None)


# ── Register sub-commands from sub-modules ───────────────────────────────────

from equinox.cli.http import get, post, put, patch, delete          # noqa: E402
from equinox.cli.collections import collection                      # noqa: E402
from equinox.cli.history import history                             # noqa: E402
from equinox.cli.environments import env                            # noqa: E402
from equinox.cli.variables import vargroup                          # noqa: E402
from equinox.cli.requests import request                            # noqa: E402
from equinox.cli.imports import import_cmd                          # noqa: E402

cli.add_command(get)
cli.add_command(post)
cli.add_command(put)
cli.add_command(patch)
cli.add_command(delete)
cli.add_command(collection)
cli.add_command(history)
cli.add_command(env)
cli.add_command(vargroup)
cli.add_command(request)
cli.add_command(import_cmd, "import")


# ── GUI command (kept here — tiny, avoids an extra file) ─────────────────────


@cli.command()
def gui():
    """Launch GUI application"""
    from equinox.gui.app import main as gui_main

    gui_main()


if __name__ == "__main__":
    cli()
