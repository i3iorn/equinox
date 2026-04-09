"""Variable group management CLI commands."""

import logging
import sys

import click

logger = logging.getLogger(__name__)


@click.group()
def vargroup():
    """Manage variable groups"""
    pass


@vargroup.command("list")
def vargroup_list():
    """List all variable groups"""
    from equinox.cli.main import get_db
    from equinox.storage import VariableGroupManager

    db = get_db()
    manager = VariableGroupManager(db)
    groups = manager.list_groups()

    if not groups:
        click.echo("No variable groups found")
        return

    for group in groups:
        click.echo(f"[{group['id']}] {group['name']}")
        if group["description"]:
            click.echo(f"    {group['description']}")


@vargroup.command("create")
@click.argument("name")
@click.option("--description", "-d", help="Group description")
def vargroup_create(name, description):
    """Create a new variable group"""
    from equinox.cli.main import get_db
    from equinox.storage import VariableGroupManager

    db = get_db()
    manager = VariableGroupManager(db)
    group_id = manager.create_group(name, description or "")
    logger.info("Created variable group: id=%s name=%r", group_id, name)
    click.echo(f"Variable group created with ID: {group_id}")


@vargroup.command("delete")
@click.argument("group_id", type=int)
def vargroup_delete(group_id):
    """Delete a variable group"""
    from equinox.cli.main import get_db
    from equinox.storage import VariableGroupManager

    db = get_db()
    manager = VariableGroupManager(db)
    manager.delete_group(group_id)
    logger.info("Deleted variable group id=%s", group_id)
    click.echo("Variable group deleted")


@vargroup.command("add-var")
@click.argument("group_id", type=int)
@click.argument("key")
@click.argument("value")
@click.option("--description", "-d", help="Variable description")
def vargroup_add_var(group_id, key, value, description):
    """Add a variable to a group"""
    from equinox.cli.main import get_db
    from equinox.storage import VariableGroupManager

    db = get_db()
    manager = VariableGroupManager(db)
    manager.add_variable(group_id, key, value, description or "")
    logger.info("Added variable %r to group id=%s", key, group_id)
    click.echo(f"Variable '{key}' added to group {group_id}")


@vargroup.command("remove-var")
@click.argument("group_id", type=int)
@click.argument("key")
def vargroup_remove_var(group_id, key):
    """Remove a variable from a group"""
    from equinox.cli.main import get_db
    from equinox.storage import VariableGroupManager

    db = get_db()
    manager = VariableGroupManager(db)
    manager.remove_variable(group_id, key)
    logger.info("Removed variable %r from group id=%s", key, group_id)
    click.echo(f"Variable '{key}' removed from group {group_id}")


@vargroup.command("show")
@click.argument("group_id", type=int)
def vargroup_show(group_id):
    """Show all variables in a group"""
    from equinox.cli.main import get_db
    from equinox.storage import VariableGroupManager

    db = get_db()
    manager = VariableGroupManager(db)
    group = manager.get_group(group_id)

    if not group:
        click.echo(f"Variable group {group_id} not found", err=True)
        sys.exit(1)

    click.echo(f"Variable Group: {group['name']}")
    if group["description"]:
        click.echo(f"Description: {group['description']}")
    click.echo()

    variables = manager.list_group_variables(group_id)
    if not variables:
        click.echo("No variables in this group")
        return

    for var in variables:
        click.echo(f"  {var['key']} = {var['value']}")
        if var["description"]:
            click.echo(f"    {var['description']}")
