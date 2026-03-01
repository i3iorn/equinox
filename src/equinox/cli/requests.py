"""Request and auth management CLI commands."""

import sys

import click

from equinox.storage import CollectionManager


@click.group()
def request():
    """Manage individual requests"""
    pass


def _get_request_or_exit(db, request_id: int):
    """Return a Request from the DB, or print an error and exit if not found."""
    manager = CollectionManager(db)
    req = manager.get_request(request_id)
    if not req:
        click.echo(f"Error: Request ID {request_id} not found", err=True)
        sys.exit(1)
    return manager, req


@request.command("run")
@click.argument("request_id", type=int)
@click.option("--quiet", "-q", is_flag=True, help="Output only the response body")
@click.pass_context
def run_request(ctx, request_id, quiet):
    """Execute a saved request by ID."""
    from equinox.cli.main import get_db
    from equinox.cli.http import _send_request

    db = get_db()
    _manager, req = _get_request_or_exit(db, request_id)

    click.echo(f"Running: {req.method} {req.url}" + (f"  ({req.name})" if req.name else ""))

    header_list = tuple(f"{k}: {v}" for k, v in (req.headers or {}).items())
    param_list = tuple(f"{k}={v}" for k, v in (req.params or {}).items())

    auth_str = None
    if req.auth:
        from equinox.auth import BasicAuth, BearerAuth, APIKeyAuth
        if isinstance(req.auth, BearerAuth):
            auth_str = f"bearer:{req.auth.token}"
        elif isinstance(req.auth, BasicAuth):
            auth_str = f"basic:{req.auth.username}:{req.auth.password}"
        elif isinstance(req.auth, APIKeyAuth):
            auth_str = f"apikey:{req.auth.location}:{req.auth.key}:{req.auth.value}"

    _send_request(
        ctx, req.method, req.url, req.body,
        header_list, param_list, auth_str,
        timeout=30.0, no_verify=False, save_name=None,
        save_response_path=None, quiet=quiet,
        collection_id=req.collection_id,
    )


@request.group()
def auth():
    """Manage request authentication"""
    pass


@auth.command("basic")
@click.argument("request_id", type=int)
@click.option("--username", "-u", prompt=True, help="Username for basic auth")
@click.option("--password", "-p", prompt=True, hide_input=True, help="Password for basic auth")
def auth_basic(request_id, username, password):
    """Set basic authentication for a request"""
    try:
        from equinox.auth import BasicAuth
        from equinox.cli.main import get_db

        db = get_db()
        manager, req = _get_request_or_exit(db, request_id)
        manager.update_request_auth(request_id, BasicAuth(username=username, password=password))
        click.echo(f"✓ Basic auth configured for request #{request_id} ({req.name})")
        click.echo(f"  Username: {username}")
    except Exception as exc:
        click.echo(f"Error setting basic auth: {exc}", err=True)
        sys.exit(1)


@auth.command("oauth2")
@click.argument("request_id", type=int)
@click.option("--token-url", required=True, help="OAuth2 token endpoint URL")
@click.option("--client-id", required=True, help="OAuth2 client ID")
@click.option("--client-secret", prompt=True, hide_input=True, help="OAuth2 client secret")
@click.option("--scope", help="OAuth2 scope (optional)")
@click.option("--access-token", help="Existing access token (optional)")
@click.option("--refresh-token", help="Refresh token (optional)")
def auth_oauth2(request_id, token_url, client_id, client_secret, scope,
                access_token, refresh_token):
    """Set OAuth2 authentication for a request"""
    try:
        from equinox.auth import OAuth2Auth
        from equinox.cli.main import get_db

        db = get_db()
        manager, req = _get_request_or_exit(db, request_id)
        auth_obj = OAuth2Auth(
            token_url=token_url, client_id=client_id,
            client_secret=client_secret, scope=scope,
            access_token=access_token, refresh_token=refresh_token,
        )
        manager.update_request_auth(request_id, auth_obj)

        click.echo(f"✓ OAuth2 configured for request #{request_id} ({req.name})")
        click.echo(f"  Token URL: {token_url}")
        click.echo(f"  Client ID: {client_id}")
        if scope:
            click.echo(f"  Scope: {scope}")
    except Exception as exc:
        click.echo(f"Error setting OAuth2: {exc}", err=True)
        sys.exit(1)


@auth.command("bearer")
@click.argument("request_id", type=int)
@click.option("--token", "-t", prompt=True, hide_input=True, help="Bearer token")
def auth_bearer(request_id, token):
    """Set bearer token authentication for a request"""
    try:
        from equinox.auth import BearerAuth
        from equinox.cli.main import get_db

        db = get_db()
        manager, req = _get_request_or_exit(db, request_id)
        manager.update_request_auth(request_id, BearerAuth(token=token))
        click.echo(f"✓ Bearer token configured for request #{request_id} ({req.name})")
    except Exception as exc:
        click.echo(f"Error setting bearer token: {exc}", err=True)
        sys.exit(1)


@auth.command("api-key")
@click.argument("request_id", type=int)
@click.option("--key-name", "-n", required=True, help="API key name/parameter")
@click.option("--key-value", "-v", prompt=True, hide_input=True, help="API key value")
@click.option("--location", "-l", type=click.Choice(["header", "query"]),
              default="header", help="Where to send the key")
def auth_api_key(request_id, key_name, key_value, location):
    """Set API key authentication for a request"""
    try:
        from equinox.auth import APIKeyAuth
        from equinox.cli.main import get_db

        db = get_db()
        manager, req = _get_request_or_exit(db, request_id)
        manager.update_request_auth(request_id, APIKeyAuth(key=key_name, value=key_value, location=location))
        click.echo(f"✓ API key configured for request #{request_id} ({req.name})")
        click.echo(f"  Key: {key_name} ({location})")
    except Exception as exc:
        click.echo(f"Error setting API key: {exc}", err=True)
        sys.exit(1)


@auth.command("clear")
@click.argument("request_id", type=int)
def auth_clear(request_id):
    """Remove authentication from a request"""
    try:
        from equinox.cli.main import get_db

        db = get_db()
        manager, req = _get_request_or_exit(db, request_id)
        manager.update_request_auth(request_id, None)
        click.echo(f"✓ Authentication cleared for request #{request_id} ({req.name})")
    except Exception as exc:
        click.echo(f"Error clearing auth: {exc}", err=True)
        sys.exit(1)


@auth.command("show")
@click.argument("request_id", type=int)
def auth_show(request_id):
    """Show authentication configuration for a request"""
    try:
        from equinox.cli.main import get_db

        db = get_db()
        _manager, req = _get_request_or_exit(db, request_id)
        click.echo(f"Request #{request_id}: {req.name}")
        click.echo(f"  Method: {req.method}")
        click.echo(f"  URL: {req.url}")
        click.echo(f"  Auth: {req.auth}" if req.auth else "  Auth: None")
    except Exception as exc:
        click.echo(f"Error showing auth: {exc}", err=True)
        sys.exit(1)
