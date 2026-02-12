"""Main CLI entry point"""

import click
import json
import sys
from pathlib import Path

from equinox import __version__
from equinox.core.client import HTTPClient
from equinox.core.request import Request
from equinox.core.exceptions import EquinoxError
from equinox.storage import Database, CollectionManager, EnvironmentManager, HistoryManager
from equinox.auth import BearerAuth, APIKeyAuth, BasicAuth, OAuth2Auth


# Global database path
DB_PATH = Path.home() / ".equinox" / "equinox.db"


def get_db():
    """Get database instance"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return Database(str(DB_PATH))


@click.group()
@click.version_option(version=__version__)
@click.option("--debug", is_flag=True, help="Enable debug mode")
@click.pass_context
def cli(ctx, debug):
    """Equinox - A local-first API testing tool"""
    ctx.ensure_object(dict)
    ctx.obj["DEBUG"] = debug


# ============================================================================
# Request Commands
# ============================================================================


@cli.command()
@click.argument("url")
@click.option("--header", "-H", multiple=True, help="Add header (format: 'Key: Value')")
@click.option("--param", "-p", multiple=True, help="Add query param (format: 'key=value')")
@click.option("--auth", "-a", help="Authentication (format: 'bearer:TOKEN' or 'basic:USER:PASS')")
@click.option("--timeout", "-t", type=float, default=30.0, help="Request timeout in seconds")
@click.option("--no-verify", is_flag=True, help="Disable SSL verification")
@click.option("--save", "-s", help="Save request to collection with this name")
@click.pass_context
def get(ctx, url, header, param, auth, timeout, no_verify, save):
    """Send GET request"""
    _send_request(ctx, "GET", url, None, header, param, auth, timeout, no_verify, save)


@cli.command()
@click.argument("url")
@click.option("--data", "-d", help="Request body (plain text or @filename)")
@click.option("--json", "-j", "json_data", help="JSON request body")
@click.option("--header", "-H", multiple=True, help="Add header (format: 'Key: Value')")
@click.option("--param", "-p", multiple=True, help="Add query param (format: 'key=value')")
@click.option("--auth", "-a", help="Authentication")
@click.option("--timeout", "-t", type=float, default=30.0, help="Request timeout")
@click.option("--no-verify", is_flag=True, help="Disable SSL verification")
@click.option("--save", "-s", help="Save request to collection with this name")
@click.pass_context
def post(ctx, url, data, json_data, header, param, auth, timeout, no_verify, save):
    """Send POST request"""
    body = _prepare_body(data, json_data)
    _send_request(ctx, "POST", url, body, header, param, auth, timeout, no_verify, save)


@cli.command()
@click.argument("url")
@click.option("--data", "-d", help="Request body")
@click.option("--json", "-j", "json_data", help="JSON request body")
@click.option("--header", "-H", multiple=True, help="Add header")
@click.option("--param", "-p", multiple=True, help="Add query param")
@click.option("--auth", "-a", help="Authentication")
@click.option("--timeout", "-t", type=float, default=30.0, help="Request timeout")
@click.option("--no-verify", is_flag=True, help="Disable SSL verification")
@click.option("--save", "-s", help="Save request to collection with this name")
@click.pass_context
def put(ctx, url, data, json_data, header, param, auth, timeout, no_verify, save):
    """Send PUT request"""
    body = _prepare_body(data, json_data)
    _send_request(ctx, "PUT", url, body, header, param, auth, timeout, no_verify, save)


@cli.command()
@click.argument("url")
@click.option("--data", "-d", help="Request body")
@click.option("--json", "-j", "json_data", help="JSON request body")
@click.option("--header", "-H", multiple=True, help="Add header")
@click.option("--param", "-p", multiple=True, help="Add query param")
@click.option("--auth", "-a", help="Authentication")
@click.option("--timeout", "-t", type=float, default=30.0, help="Request timeout")
@click.option("--no-verify", is_flag=True, help="Disable SSL verification")
@click.option("--save", "-s", help="Save request to collection with this name")
@click.pass_context
def patch(ctx, url, data, json_data, header, param, auth, timeout, no_verify, save):
    """Send PATCH request"""
    body = _prepare_body(data, json_data)
    _send_request(ctx, "PATCH", url, body, header, param, auth, timeout, no_verify, save)


@cli.command()
@click.argument("url")
@click.option("--header", "-H", multiple=True, help="Add header")
@click.option("--param", "-p", multiple=True, help="Add query param")
@click.option("--auth", "-a", help="Authentication")
@click.option("--timeout", "-t", type=float, default=30.0, help="Request timeout")
@click.option("--no-verify", is_flag=True, help="Disable SSL verification")
@click.pass_context
def delete(ctx, url, header, param, auth, timeout, no_verify):
    """Send DELETE request"""
    _send_request(ctx, "DELETE", url, None, header, param, auth, timeout, no_verify, None)


def _prepare_body(data, json_data):
    """Prepare request body from data or JSON"""
    if json_data:
        return json_data
    elif data:
        if data.startswith("@"):
            # Read from file
            file_path = Path(data[1:])
            if file_path.exists():
                return file_path.read_text()
        return data
    return None


def _send_request(ctx, method, url, body, headers, params, auth, timeout, no_verify, save_name):
    """Send HTTP request and print response"""
    try:
        # Parse headers
        header_dict = {}
        for h in headers:
            if ":" in h:
                key, value = h.split(":", 1)
                header_dict[key.strip()] = value.strip()

        # Parse params
        param_dict = {}
        for p in params:
            if "=" in p:
                key, value = p.split("=", 1)
                param_dict[key.strip()] = value.strip()

        # Parse auth
        auth_obj = None
        if auth:
            auth_obj = _parse_auth(auth)

        # Create request
        request = Request(
            method=method,
            url=url,
            headers=header_dict,
            params=param_dict,
            body=body,
            auth=auth_obj,
            timeout=timeout,
            verify_ssl=not no_verify,
        )

        # Save request if requested
        if save_name:
            db = get_db()
            collection_mgr = CollectionManager(db)
            request.name = save_name
            req_id = collection_mgr.save_request(request)
            click.echo(f"Request saved with ID: {req_id}")

        # Send request
        client = HTTPClient(timeout=timeout, verify_ssl=not no_verify)
        response = client.send(request)

        # Save to history
        db = get_db()
        history_mgr = HistoryManager(db)
        history_mgr.save_history(request, response)

        # Print response
        _print_response(response, ctx.obj.get("DEBUG", False))

    except EquinoxError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        if ctx.obj.get("DEBUG"):
            raise
        click.echo(f"Unexpected error: {e}", err=True)
        sys.exit(1)


def _parse_auth(auth_str):
    """Parse authentication string"""
    if auth_str.startswith("bearer:"):
        token = auth_str[7:]
        return BearerAuth(token)
    elif auth_str.startswith("basic:"):
        parts = auth_str[6:].split(":")
        if len(parts) == 2:
            return BasicAuth(parts[0], parts[1])
    elif auth_str.startswith("apikey:"):
        # Format: apikey:header:X-API-Key:value or apikey:query:api_key:value
        parts = auth_str[7:].split(":", 2)
        if len(parts) == 3:
            location, key, value = parts
            return APIKeyAuth(key, value, location)
    raise click.BadParameter(f"Invalid auth format: {auth_str}")


def _print_response(response, debug=False):
    """Print response details"""
    # Status line
    status_color = "green" if response.status_code < 400 else "red"
    click.secho(f"HTTP {response.status_code} {response.reason}", fg=status_color, bold=True)
    click.echo(f"Time: {response.elapsed:.3f}s")
    click.echo(f"Size: {response.size} bytes")
    click.echo()

    # Headers (in debug mode)
    if debug:
        click.secho("Response Headers:", bold=True)
        for key, value in response.headers.items():
            click.echo(f"  {key}: {value}")
        click.echo()

    # Body
    click.secho("Response Body:", bold=True)
    if response.is_json:
        try:
            data = response.json()
            click.echo(json.dumps(data, indent=2))
        except:
            click.echo(response.text)
    else:
        click.echo(response.text)


# ============================================================================
# Collection Commands
# ============================================================================


@cli.group()
def collection():
    """Manage request collections"""
    pass


@collection.command("list")
def collection_list():
    """List all collections"""
    db = get_db()
    mgr = CollectionManager(db)
    collections = mgr.list_collections()

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
    db = get_db()
    mgr = CollectionManager(db)
    col_id = mgr.create_collection(name, description or "")
    click.echo(f"Collection created with ID: {col_id}")


@collection.command("delete")
@click.argument("collection_id", type=int)
def collection_delete(collection_id):
    """Delete a collection"""
    db = get_db()
    mgr = CollectionManager(db)
    mgr.delete_collection(collection_id)
    click.echo("Collection deleted")


@collection.command("requests")
@click.argument("collection_id", type=int)
def collection_requests(collection_id):
    """List requests in a collection"""
    db = get_db()
    mgr = CollectionManager(db)
    requests = mgr.list_requests(collection_id)

    if not requests:
        click.echo("No requests in this collection")
        return

    for req in requests:
        click.echo(f"[{req['id']}] {req['method']} {req['name']}")
        click.echo(f"    {req['url']}")


# ============================================================================
# History Commands
# ============================================================================


@cli.command()
@click.option("--limit", "-n", type=int, default=20, help="Number of entries to show")
def history(limit):
    """View request history"""
    db = get_db()
    mgr = HistoryManager(db)
    entries = mgr.list_history(limit=limit)

    if not entries:
        click.echo("No history found")
        return

    for entry in entries:
        status_color = "green" if entry.get("status_code", 0) < 400 else "red"
        click.secho(
            f"[{entry['id']}] {entry['method']} {entry['url']}", fg=status_color
        )
        if entry.get("status_code"):
            click.echo(f"    Status: {entry['status_code']} | Time: {entry['elapsed']:.3f}s")
        if entry.get("error"):
            click.echo(f"    Error: {entry['error']}")
        click.echo(f"    Executed: {entry['executed_at']}")
        click.echo()


# ============================================================================
# Environment Commands
# ============================================================================


@cli.group()
def env():
    """Manage environments"""
    pass


@env.command("list")
def env_list():
    """List all environments"""
    db = get_db()
    mgr = EnvironmentManager(db)
    envs = mgr.list_environments()

    if not envs:
        click.echo("No environments found")
        return

    for e in envs:
        active = " (active)" if e["is_active"] else ""
        click.echo(f"[{e['id']}] {e['name']}{active}")
        if e["description"]:
            click.echo(f"    {e['description']}")


@env.command("create")
@click.argument("name")
@click.option("--var", "-v", multiple=True, help="Variable (format: key=value)")
@click.option("--description", "-d", help="Environment description")
def env_create(name, var, description):
    """Create a new environment"""
    variables = {}
    for v in var:
        if "=" in v:
            key, value = v.split("=", 1)
            variables[key.strip()] = value.strip()

    db = get_db()
    mgr = EnvironmentManager(db)
    env_id = mgr.create_environment(name, variables, description or "")
    click.echo(f"Environment created with ID: {env_id}")


@env.command("activate")
@click.argument("environment_id", type=int)
def env_activate(environment_id):
    """Activate an environment"""
    db = get_db()
    mgr = EnvironmentManager(db)
    mgr.set_active_environment(environment_id)
    click.echo("Environment activated")


# ============================================================================
# GUI Command
# ============================================================================


@cli.command()
def gui():
    """Launch GUI application"""
    try:
        from equinox.gui.app import main as gui_main

        gui_main()
    except ImportError:
        click.echo("GUI dependencies not installed. Install with: pip install equinox[gui]")
        sys.exit(1)


if __name__ == "__main__":
    cli()
