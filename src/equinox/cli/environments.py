"""Environment management CLI commands."""

import logging
import sys

import click

from equinox.storage import EnvironmentManager

logger = logging.getLogger(__name__)


@click.group()
def env():
    """Manage environments"""
    pass


@env.command("list")
def env_list():
    """List all environments"""
    from equinox.cli.main import get_db
    db = get_db()
    manager = EnvironmentManager(db)
    environments = manager.list_environments()

    if not environments:
        click.echo("No environments found")
        return

    for environment in environments:
        active = " (active)" if environment["is_active"] else ""
        click.echo(f"[{environment['id']}] {environment['name']}{active}")
        if environment["description"]:
            click.echo(f"    {environment['description']}")


@env.command("create")
@click.argument("name")
@click.option("--var", "-v", multiple=True, help="Variable (format: key=value)")
@click.option("--description", "-d", help="Environment description")
def env_create(name, var, description):
    """Create a new environment"""
    variables = {}
    for var_str in var:
        if "=" in var_str:
            key, value = var_str.split("=", 1)
            variables[key.strip()] = value.strip()

    from equinox.cli.main import get_db
    db = get_db()
    manager = EnvironmentManager(db)
    env_id = manager.create_environment(name, variables, description or "")
    logger.info("Created environment: id=%s name=%r", env_id, name)
    click.echo(f"Environment created with ID: {env_id}")


@env.command("activate")
@click.argument("environment_id", type=int)
def env_activate(environment_id):
    """Activate an environment"""
    from equinox.cli.main import get_db
    db = get_db()
    manager = EnvironmentManager(db)
    manager.set_active_environment(environment_id)
    logger.info("Activated environment id=%s", environment_id)
    click.echo("Environment activated")


@env.command("delete")
@click.argument("environment_id", type=int)
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation")
def env_delete(environment_id, yes):
    """Delete an environment"""
    from equinox.cli.main import get_db
    db = get_db()
    manager = EnvironmentManager(db)
    environment = manager.get_environment(environment_id)
    if not environment:
        logger.error("Environment %s not found for delete", environment_id)
        click.echo(f"Environment {environment_id} not found", err=True)
        sys.exit(1)
    if not yes:
        click.confirm(f"Delete environment '{environment['name']}'?", abort=True)
    manager.delete_environment(environment_id)
    logger.info("Deleted environment id=%s name=%r", environment_id, environment['name'])
    click.echo("Environment deleted")


@env.command("show")
@click.argument("environment_id", type=int)
def env_show(environment_id):
    """Show environment details and variables"""
    from equinox.cli.main import get_db
    db = get_db()
    manager = EnvironmentManager(db)
    environment = manager.get_environment(environment_id)
    if not environment:
        click.echo(f"Environment {environment_id} not found", err=True)
        sys.exit(1)

    active = " (active)" if environment.get("is_active") else ""
    click.echo(f"Environment: {environment['name']}{active}")
    if environment.get("description"):
        click.echo(f"Description: {environment['description']}")
    click.echo()

    variables = environment.get("variables", {})
    if not variables:
        click.echo("No variables defined")
        return

    click.echo("Variables:")
    for key, value in sorted(variables.items()):
        click.echo(f"  {key} = {value}")


@env.command("set-var")
@click.argument("environment_id", type=int)
@click.argument("key")
@click.argument("value")
def env_set_var(environment_id, key, value):
    """Set a variable in an environment"""
    from equinox.cli.main import get_db
    db = get_db()
    manager = EnvironmentManager(db)
    environment = manager.get_environment(environment_id)
    if not environment:
        click.echo(f"Environment {environment_id} not found", err=True)
        sys.exit(1)

    variables = environment.get("variables", {})
    variables[key] = value
    manager.update_environment(environment_id, variables=variables)
    logger.info("Set variable %r in environment id=%s", key, environment_id)
    click.echo(f"Variable '{key}' set in environment '{environment['name']}'")


@env.command("remove-var")
@click.argument("environment_id", type=int)
@click.argument("key")
def env_remove_var(environment_id, key):
    """Remove a variable from an environment"""
    from equinox.cli.main import get_db
    db = get_db()
    manager = EnvironmentManager(db)
    environment = manager.get_environment(environment_id)
    if not environment:
        click.echo(f"Environment {environment_id} not found", err=True)
        sys.exit(1)

    variables = environment.get("variables", {})
    if key not in variables:
        click.echo(f"Variable '{key}' not found in environment '{environment['name']}'", err=True)
        sys.exit(1)

    del variables[key]
    manager.update_environment(environment_id, variables=variables)
    logger.info("Removed variable %r from environment id=%s", key, environment_id)
    click.echo(f"Variable '{key}' removed from environment '{environment['name']}'")


from equinox.core.dotenv import parse_dotenv as _parse_dotenv


@env.command("import-dotenv")
@click.argument("environment_id", type=int)
@click.argument("file", type=click.Path(exists=True, readable=True))
@click.option("--merge/--replace", default=True,
              help="Merge with existing variables (default) or replace all")
def env_import_dotenv(environment_id, file, merge):
    """Import variables from a .env file into an environment.

    By default the imported variables are merged with any existing ones.
    Pass --replace to overwrite all existing variables with the file contents.
    """
    from pathlib import Path
    from equinox.cli.main import get_db
    db = get_db()
    manager = EnvironmentManager(db)
    environment = manager.get_environment(environment_id)
    if not environment:
        click.echo(f"Environment {environment_id} not found", err=True)
        sys.exit(1)

    try:
        text = Path(file).read_text(encoding="utf-8", errors="replace")
        new_vars = _parse_dotenv(text)
    except Exception as exc:
        click.secho(f"Failed to read .env file: {exc}", fg="red", err=True)
        sys.exit(1)

    if not new_vars:
        click.echo("No variables found in the .env file.")
        return

    existing = dict(environment.get("variables", {})) if merge else {}
    existing.update(new_vars)
    manager.update_environment(environment_id, variables=existing)
    logger.info(
        "Imported %d variable(s) into environment id=%s (merge=%s)",
        len(new_vars), environment_id, merge,
    )

    click.echo(f"Imported {len(new_vars)} variable(s) into '{environment['name']}':")
    for k, v in sorted(new_vars.items()):
        display = v[:60] + "…" if len(v) > 60 else v
        click.echo(f"  {k} = {display}")
