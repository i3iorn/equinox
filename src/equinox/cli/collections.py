"""Collection management CLI commands."""
import json
import logging
import sys

import click

from equinox.storage import CollectionManager
from equinox.core.redact import redact_body as _redact

logger = logging.getLogger(__name__)


@click.group()
def collection():
    """Manage request collections"""
    pass


@collection.command("list")
def collection_list():
    """List all collections"""
    from equinox.cli.main import get_db
    db = get_db()
    manager = CollectionManager(db)
    collections = manager.list_collections()

    if not collections:
        click.echo("No collections found")
        return

    for col in collections:
        click.echo(f"[{col['id']}] {col['name']}")
        if col["description"]:
            click.echo(f"    {col['description']}")


@collection.command("create")
@click.argument("name")
@click.option("--description", "-d", help="Collection description")
def collection_create(name, description):
    """Create a new collection"""
    from equinox.cli.main import get_db
    db = get_db()
    manager = CollectionManager(db)
    collection_id = manager.create_collection(name, description or "")
    logger.info("Created collection: id=%s name=%r", collection_id, name)
    click.echo(f"Collection created with ID: {collection_id}")


@collection.command("delete")
@click.argument("collection_id", type=int)
def collection_delete(collection_id):
    """Delete a collection"""
    from equinox.cli.main import get_db
    db = get_db()
    manager = CollectionManager(db)
    manager.delete_collection(collection_id)
    logger.info("Deleted collection id=%s", collection_id)
    click.echo("Collection deleted")


@collection.command("run")
@click.argument("collection_id", type=int)
@click.option("--env", "env_id", type=int, default=None,
              help="Environment ID — its variables are used for interpolation")
@click.option("--stop-on-error", is_flag=True, default=False,
              help="Stop after the first failed request")
@click.option("--timeout", default=30.0, type=float, show_default=True,
              help="Per-request timeout in seconds")
def collection_run(collection_id, env_id, stop_on_error, timeout):
    """Run all requests in a collection and print the results.

    Each request is sent sequentially.  Exit code is 0 if all requests
    succeed (HTTP < 400) and 1 if any request fails or errors.
    """
    from equinox.cli.main import get_db
    from equinox.core.client import HTTPClient
    from equinox.core.request import Request as _Req
    from equinox.core.interpolation import VariableInterpolator

    db = get_db()
    manager = CollectionManager(db)
    col = manager.get_collection(collection_id)
    if not col:
        logger.error("Collection %s not found for run", collection_id)
        click.secho(f"Collection {collection_id} not found", fg="red", err=True)
        raise click.Exit(1)

    req_rows = manager.list_requests(collection_id)
    if not req_rows:
        click.echo("No requests in this collection.")
        return

    variables: dict = {}
    if env_id is not None:
        from equinox.storage import EnvironmentManager
        env = EnvironmentManager(db).get_environment(env_id)
        if env:
            variables.update(env.get("variables", {}))
        else:
            logger.warning("Environment %s not found — running collection without variables", env_id)
            click.secho(f"Environment {env_id} not found — running without variables",
                        fg="yellow", err=True)

    logger.info(
        "Running collection id=%s name=%r (%d requests, env_id=%s)",
        collection_id, col['name'], len(req_rows), env_id,
    )

    click.echo(f"Running collection '{col['name']}' ({len(req_rows)} request(s))")
    if env_id:
        click.echo(f"  Environment: {env['name'] if env else env_id}")
    click.echo()

    passed = failed = 0
    for req_row in req_rows:
        name   = req_row.get("name") or "Unnamed"
        method = req_row.get("method") or "GET"
        url    = req_row.get("url") or ""
        raw_headers = req_row.get("headers") or {}
        if isinstance(raw_headers, str):
            try:
                raw_headers = json.loads(raw_headers)
            except (json.JSONDecodeError, TypeError):
                raw_headers = {}
        headers = dict(raw_headers)
        body    = req_row.get("body")

        # Interpolate variables
        try:
            url = VariableInterpolator.interpolate(url, variables)
            headers = {
                VariableInterpolator.interpolate(k, variables):
                VariableInterpolator.interpolate(v, variables)
                for k, v in headers.items()
            }
            if body:
                body = VariableInterpolator.interpolate(body, variables)
        except Exception:
            pass

        click.echo(f"  [{method}] {name}")
        click.echo(f"       {url}")

        try:
            req = _Req(method=method, url=url, headers=headers, body=body, timeout=timeout)
            client = HTTPClient(timeout=timeout)
            resp = client.send(req)
            elapsed_ms = int(resp.elapsed * 1000)
            sc = resp.status_code
            if sc < 400:
                click.secho(f"       {sc} {resp.reason}  ({elapsed_ms} ms)", fg="green")
                passed += 1
            elif sc < 500:
                click.secho(f"       {sc} {resp.reason}  ({elapsed_ms} ms)", fg="yellow")
                failed += 1
            else:
                click.secho(f"       {sc} {resp.reason}  ({elapsed_ms} ms)", fg="red")
                failed += 1
        except Exception as exc:
            logger.error("Request '%s' [%s %s] error: %s", name, method, url, exc)
            click.secho(f"       ERROR: {_redact(str(exc))}", fg="red")
            failed += 1

        if stop_on_error and failed:
            click.echo()
            click.secho("Stopped after first failure (--stop-on-error)", fg="yellow")
            break

    click.echo()
    total = passed + failed
    if failed:
        logger.warning("Collection run finished: %d/%d passed, %d failed", passed, total, failed)
        click.secho(f"Results: {passed}/{total} passed, {failed} failed", fg="red")
        raise click.Exit(1)
    else:
        logger.info("Collection run finished: %d/%d passed", passed, total)
        click.secho(f"Results: {passed}/{total} passed", fg="green")


@collection.command("export")
@click.argument("collection_id", type=int)
@click.option("--format", "-f",
              type=click.Choice(["postman", "openapi", "insomnia", "har"]),
              default="postman", help="Export format")
@click.option("--output", "-o", type=click.Path(), required=True, help="Output file path")
@click.option("--title", "-t", help="API title (for OpenAPI)")
@click.option("--version", "-v", default="1.0.0", help="API version (for OpenAPI)")
def collection_export(collection_id, format, output, title, version):
    """Export collection in various formats.

    Supported formats: postman, openapi, insomnia, har
    """
    from pathlib import Path
    from equinox.cli.main import get_db
    from equinox.importers.exporters import (
        PostmanExporter, OpenAPIExporter, InsomniaExporter,
    )

    try:
        db = get_db()
        output_path = Path(output)

        if format == "postman":
            data = PostmanExporter.export_collection(db, collection_id)
            PostmanExporter.export_to_file(data, output_path)
            logger.info("Exported collection %s to %s (postman)", collection_id, output_path)
            click.echo(f"✓ Collection exported to {output_path} (Postman format)")
        elif format == "openapi":
            if not title:
                title = f"API Collection {collection_id}"
            data = OpenAPIExporter.export_collection(db, collection_id, title, version)
            OpenAPIExporter.export_to_file(data, output_path)
            logger.info("Exported collection %s to %s (openapi)", collection_id, output_path)
            click.echo(f"✓ Collection exported to {output_path} (OpenAPI 3.0 format)")
        elif format == "insomnia":
            data = InsomniaExporter.export_collection(db, collection_id)
            InsomniaExporter.export_to_file(data, output_path)
            logger.info("Exported collection %s to %s (insomnia)", collection_id, output_path)
            click.echo(f"✓ Collection exported to {output_path} (Insomnia format)")
        elif format == "har":
            click.echo("HAR export requires request/response history. "
                       "Use 'equinox history export' instead.")
    except Exception as exc:
        logger.error("Export failed for collection %s (format=%s): %s", collection_id, format, exc)
        click.secho(f"✗ Export failed: {exc}", fg="red", err=True)
        raise click.Exit(1)


@collection.command("requests")
@click.argument("collection_id", type=int)
def collection_requests(collection_id):
    """List requests in a collection"""
    from equinox.cli.main import get_db
    db = get_db()
    manager = CollectionManager(db)
    requests = manager.list_requests(collection_id)

    if not requests:
        click.echo("No requests in this collection")
        return

    for req in requests:
        click.echo(f"[{req['id']}] {req['method']} {req['name']}")
        click.echo(f"    {req['url']}")


# ── Collection variable sub-commands ─────────────────────────────────────────


@collection.command("add-var")
@click.argument("collection_id", type=int)
@click.argument("key")
@click.argument("value")
@click.option("--description", "-d", help="Variable description")
def collection_add_var(collection_id, key, value, description):
    """Add a variable to a collection"""
    from equinox.cli.main import get_db
    db = get_db()
    manager = CollectionManager(db)
    manager.add_variable(collection_id, key, value, description or "")
    click.echo(f"Variable '{key}' added to collection {collection_id}")


@collection.command("remove-var")
@click.argument("collection_id", type=int)
@click.argument("key")
def collection_remove_var(collection_id, key):
    """Remove a variable from a collection"""
    from equinox.cli.main import get_db
    db = get_db()
    manager = CollectionManager(db)
    manager.remove_variable(collection_id, key)
    click.echo(f"Variable '{key}' removed from collection {collection_id}")


@collection.command("show-vars")
@click.argument("collection_id", type=int)
def collection_show_vars(collection_id):
    """Show all variables for a collection"""
    from equinox.cli.main import get_db
    db = get_db()
    manager = CollectionManager(db)
    col = manager.get_collection(collection_id)

    if not col:
        click.echo(f"Collection {collection_id} not found", err=True)
        sys.exit(1)

    click.echo(f"Collection: {col['name']}")
    click.echo()

    variables = manager.list_collection_variables(collection_id)
    if variables:
        click.echo("Collection Variables:")
        for var in variables:
            click.echo(f"  {var['key']} = {var['value']}")
            if var["description"]:
                click.echo(f"    {var['description']}")
        click.echo()

    groups = manager.list_collection_variable_groups(collection_id)
    if groups:
        click.echo("Variable Groups:")
        for group in groups:
            click.echo(f"  [{group['id']}] {group['name']} (priority: {group['priority']})")
        click.echo()

    all_vars = manager.get_all_collection_variables(collection_id)
    if all_vars:
        click.echo("All Variables (merged):")
        for key, value in sorted(all_vars.items()):
            click.echo(f"  {key} = {value}")
    elif not variables and not groups:
        click.echo("No variables in this collection")


@collection.command("add-group")
@click.argument("collection_id", type=int)
@click.argument("group_id", type=int)
@click.option("--priority", "-p", type=int, default=0, help="Priority (lower = higher)")
def collection_add_group(collection_id, group_id, priority):
    """Add a variable group to a collection"""
    from equinox.cli.main import get_db
    db = get_db()
    manager = CollectionManager(db)
    manager.add_variable_group(collection_id, group_id, priority)
    click.echo(f"Variable group {group_id} added to collection {collection_id}")


@collection.command("remove-group")
@click.argument("collection_id", type=int)
@click.argument("group_id", type=int)
def collection_remove_group(collection_id, group_id):
    """Remove a variable group from a collection"""
    from equinox.cli.main import get_db
    db = get_db()
    manager = CollectionManager(db)
    manager.remove_variable_group(collection_id, group_id)
    click.echo(f"Variable group {group_id} removed from collection {collection_id}")
