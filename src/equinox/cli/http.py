"""HTTP request commands — get, post, put, patch, delete."""

import json
import logging
import sys
from pathlib import Path

import click

from equinox.core.client import HTTPClient
from equinox.core.request import Request
from equinox.core.exceptions import EquinoxError
from equinox.core.interpolation import VariableInterpolator
from equinox.auth import BearerAuth, APIKeyAuth, BasicAuth
from equinox.storage import CollectionManager, HistoryManager
from equinox.core.redact import redact_headers, redact_body

logger = logging.getLogger(__name__)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _prepare_body(data, json_data):
    """Prepare request body from data or JSON."""
    if json_data:
        return json_data
    if not data:
        return None
    if data.startswith("@"):
        from equinox.core.validation import Validator
        from equinox.core.exceptions import ValidationError
        try:
            file_path = Validator.validate_file_path(data[1:])
        except ValidationError as exc:
            raise click.BadParameter(f"Invalid file path: {exc}")
        if not file_path.exists():
            raise click.BadParameter(f"File not found: {data[1:]}")
        return file_path.read_text()
    return data


def _parse_auth(auth_str):
    """Parse authentication string (e.g. ``bearer:TOKEN``, ``basic:USER:PASS``)."""
    if auth_str.startswith("bearer:"):
        return BearerAuth(auth_str[7:])
    if auth_str.startswith("basic:"):
        parts = auth_str[6:].split(":")
        if len(parts) == 2:
            return BasicAuth(parts[0], parts[1])
    if auth_str.startswith("apikey:"):
        parts = auth_str[7:].split(":", 2)
        if len(parts) == 3:
            location, key, value = parts
            return APIKeyAuth(key, value, location)
    raise click.BadParameter(f"Invalid auth format: {auth_str}")


def _print_response(response, debug=False, quiet=False, fmt="text"):
    """Print response details to the terminal.

    ``fmt`` is one of ``"text"`` (default), ``"json"``, or ``"body"``.
    """
    if fmt == "json":
        _print_response_json(response)
        return

    if quiet:
        if response.is_json:
            try:
                click.echo(json.dumps(response.json(), indent=2))
            except Exception:
                click.echo(response.text)
        else:
            click.echo(response.text)
        return

    if debug:
        click.secho("Sent Request:", bold=True)
        click.echo(f"  {response.request.method} {response.sent_url or response.request.url}")
        sent_hdrs = redact_headers(response.sent_headers or response.request.headers or {})
        for key, value in sent_hdrs.items():
            click.echo(f"  {key}: {value}")
        if response.request.body:
            click.echo(f"  Body: {redact_body(response.request.body[:200], max_length=200)}")
        click.echo()

    status_color = "green" if response.status_code < 400 else "red"
    click.secho(f"HTTP {response.status_code} {response.reason}", fg=status_color, bold=True)
    click.echo(f"Time: {response.elapsed:.3f}s")
    click.echo(f"Size: {response.size} bytes")
    click.echo()

    if debug:
        click.secho("Response Headers:", bold=True)
        for key, value in redact_headers(dict(response.headers)).items():
            click.echo(f"  {key}: {value}")
        click.echo()

    click.secho("Response Body:", bold=True)
    if response.is_json:
        try:
            click.echo(json.dumps(response.json(), indent=2))
        except Exception:
            click.echo(response.text)
    else:
        click.echo(response.text)


def _print_response_json(response) -> None:
    """Emit a single JSON object with full response metadata (for scripting/piping)."""
    req = response.request
    sent_hdrs = redact_headers(response.sent_headers or req.headers or {})
    try:
        body_parsed = response.json()
    except Exception:
        body_parsed = None

    output = {
        "request": {
            "method": req.method,
            "url": response.sent_url or req.url,
            "headers": dict(sent_hdrs),
            "body": redact_body(req.body) if req.body else None,
        },
        "response": {
            "status_code": response.status_code,
            "reason": response.reason,
            "elapsed_s": round(response.elapsed, 4),
            "size_bytes": response.size,
            "headers": redact_headers(dict(response.headers)),
            "body": body_parsed if body_parsed is not None else response.text,
        },
    }
    click.echo(json.dumps(output, indent=2, ensure_ascii=False))


def _run_assertions(assert_status, assert_contains, assert_header, response):
    """Evaluate CLI assertion flags.  Returns True if all pass, False otherwise."""
    from equinox.core.assertions import evaluate_assertion

    rules = []
    if assert_status:
        rules.append({"type": "status", "field": "", "expected": assert_status})
    for text in (assert_contains or []):
        rules.append({"type": "body_contains", "field": "", "expected": text})
    for hdr in (assert_header or []):
        if ":" in hdr:
            name, value = hdr.split(":", 1)
            rules.append({"type": "header_value", "field": name.strip(), "expected": value.strip()})

    if not rules:
        return True

    all_pass = True
    for rule in rules:
        passed, msg = evaluate_assertion(rule, response)
        icon = "✓" if passed else "✗"
        click.secho(f"  {icon} {msg}", fg="green" if passed else "red")
        if not passed:
            all_pass = False

    return all_pass


def _send_request(ctx, method, url, body, headers, params, auth, timeout,
                   no_verify, save_name, save_response_path=None, quiet=False,
                   collection_id=None, output_fmt="text",
                   assert_status=None, assert_contains=(), assert_header=()):
    """Build, send, and print an HTTP request."""
    try:
        from equinox.cli.main import get_db, get_interpolation_variables

        db = get_db()
        variables = get_interpolation_variables(db, collection_id=collection_id)

        url = VariableInterpolator.interpolate(url, variables)

        header_dict = {}
        for header_str in headers:
            if ":" in header_str:
                key, value = header_str.split(":", 1)
                header_dict[VariableInterpolator.interpolate(key.strip(), variables)] = \
                    VariableInterpolator.interpolate(value.strip(), variables)

        param_dict = {}
        for param_str in params:
            if "=" in param_str:
                key, value = param_str.split("=", 1)
                param_dict[VariableInterpolator.interpolate(key.strip(), variables)] = \
                    VariableInterpolator.interpolate(value.strip(), variables)

        auth_obj = None
        if auth:
            auth = VariableInterpolator.interpolate(auth, variables)
            auth_obj = _parse_auth(auth)

        if body:
            body = VariableInterpolator.interpolate(body, variables)

        request = Request(
            method=method, url=url, headers=header_dict, params=param_dict,
            body=body, auth=auth_obj, timeout=timeout, verify_ssl=not no_verify,
        )

        logger.info("Sending %s %s", method, url, extra={
            "method": method,
            "url": url,
            "header_count": len(header_dict),
            "has_body": bool(body),
            "timeout": timeout,
            "verify_ssl": not no_verify,
        })

        if save_name:
            collection_mgr = CollectionManager(db)
            request.name = save_name
            collections = collection_mgr.list_collections()
            if not collections:
                default_id = collection_mgr.create_collection(
                    "My Requests", "Default collection for saved requests")
                col_name = "My Requests"
                click.echo(f"Created default collection '{col_name}' (ID: {default_id})")
            else:
                default_id = collections[0]["id"]
                col_name = collections[0]["name"]
            req_id = collection_mgr.save_request(request, collection_id=default_id)
            logger.info("Request saved to collection %r (id=%s, request_id=%s)",
                        col_name, default_id, req_id)
            click.echo(f"✓ Request '{save_name}' saved with ID: {req_id} to collection '{col_name}'")

        client = HTTPClient(timeout=timeout, verify_ssl=not no_verify)
        response = client.send(request)

        logger.info("Response received: HTTP %d %s in %.3fs",
                    response.status_code, response.reason, response.elapsed,
                    extra={
                        "status_code": response.status_code,
                        "reason": response.reason,
                        "elapsed_s": round(response.elapsed, 4),
                        "size_bytes": response.size,
                        "method": method,
                        "url": url,
                    })

        HistoryManager(db).save_history(request, response)

        if save_response_path:
            out_path = Path(save_response_path)
            body_content = response.text if hasattr(response, "text") else str(response.body)
            out_path.write_text(body_content, encoding="utf-8")
            logger.debug("Response body saved to %s (%d bytes)", out_path, len(body_content))
            click.echo(f"✓ Response saved to {out_path}")

        _print_response(response, ctx.obj.get("DEBUG", False), quiet=quiet, fmt=output_fmt)

        if assert_status or assert_contains or assert_header:
            all_pass = _run_assertions(assert_status, assert_contains, assert_header, response)
            logger.info("CLI assertions: %s", "all passed" if all_pass else "one or more FAILED")
            if not all_pass:
                sys.exit(2)

    except EquinoxError as exc:
        logger.error("Request failed (%s): %s", type(exc).__name__, exc)
        click.echo(f"Error: {redact_body(str(exc))}", err=True)
        sys.exit(1)
    except Exception as exc:
        logger.error("Unexpected error during CLI request: %s", exc, exc_info=True)
        if ctx.obj.get("DEBUG"):
            raise
        click.echo(f"Unexpected error: {redact_body(str(exc))}", err=True)
        sys.exit(1)


# ── Click commands ───────────────────────────────────────────────────────────

_REQ_OPTIONS = [
    click.option("--header", "-H", multiple=True, help="Add header (format: 'Key: Value')"),
    click.option("--param", "-p", multiple=True, help="Add query param (format: 'key=value')"),
    click.option("--auth", "-a", help="Authentication (format: 'bearer:TOKEN' or 'basic:USER:PASS')"),
    click.option("--timeout", "-t", type=float, default=30.0, help="Request timeout in seconds"),
    click.option("--no-verify", is_flag=True, help="Disable SSL verification"),
    click.option("--save", "-s", help="Save request to collection with this name"),
    click.option("--save-response", "-r", type=click.Path(), default=None, help="Save response body to file"),
    click.option("--quiet", "-q", is_flag=True, help="Output only the response body (for piping)"),
    click.option(
        "--format", "-f", "output_fmt",
        type=click.Choice(["text", "json"], case_sensitive=False),
        default="text",
        help="Output format: 'text' (default) or 'json' (full metadata as JSON object)",
    ),
    click.option("--assert-status", "assert_status", default=None,
                 help="Assert response status code equals VALUE (e.g. 200). Exits 2 on failure."),
    click.option("--assert-contains", "assert_contains", multiple=True,
                 help="Assert response body contains TEXT. Exits 2 on failure. Repeatable."),
    click.option("--assert-header", "assert_header", multiple=True,
                 help="Assert response header (format: 'Name: value'). Exits 2 on failure. Repeatable."),
]


def _apply_options(cmd, options):
    for opt in reversed(options):
        cmd = opt(cmd)
    return cmd


@click.command()
@click.argument("url")
@click.pass_context
def get(ctx, url, header, param, auth, timeout, no_verify, save, save_response, quiet, output_fmt,
        assert_status, assert_contains, assert_header):
    """Send GET request"""
    _send_request(ctx, "GET", url, None, header, param, auth, timeout, no_verify, save,
                  save_response, quiet, output_fmt=output_fmt,
                  assert_status=assert_status, assert_contains=assert_contains,
                  assert_header=assert_header)

get = _apply_options(get, _REQ_OPTIONS)


@click.command()
@click.argument("url")
@click.option("--data", "-d", help="Request body (plain text or @filename)")
@click.option("--json", "-j", "json_data", help="JSON request body")
@click.pass_context
def post(ctx, url, data, json_data, header, param, auth, timeout, no_verify, save, save_response,
         quiet, output_fmt, assert_status, assert_contains, assert_header):
    """Send POST request"""
    _send_request(ctx, "POST", url, _prepare_body(data, json_data),
                  header, param, auth, timeout, no_verify, save, save_response, quiet,
                  output_fmt=output_fmt, assert_status=assert_status,
                  assert_contains=assert_contains, assert_header=assert_header)

post = _apply_options(post, _REQ_OPTIONS)


@click.command()
@click.argument("url")
@click.option("--data", "-d", help="Request body")
@click.option("--json", "-j", "json_data", help="JSON request body")
@click.pass_context
def put(ctx, url, data, json_data, header, param, auth, timeout, no_verify, save, save_response,
        quiet, output_fmt, assert_status, assert_contains, assert_header):
    """Send PUT request"""
    _send_request(ctx, "PUT", url, _prepare_body(data, json_data),
                  header, param, auth, timeout, no_verify, save, save_response, quiet,
                  output_fmt=output_fmt, assert_status=assert_status,
                  assert_contains=assert_contains, assert_header=assert_header)

put = _apply_options(put, _REQ_OPTIONS)


@click.command()
@click.argument("url")
@click.option("--data", "-d", help="Request body")
@click.option("--json", "-j", "json_data", help="JSON request body")
@click.pass_context
def patch(ctx, url, data, json_data, header, param, auth, timeout, no_verify, save, save_response,
          quiet, output_fmt, assert_status, assert_contains, assert_header):
    """Send PATCH request"""
    _send_request(ctx, "PATCH", url, _prepare_body(data, json_data),
                  header, param, auth, timeout, no_verify, save, save_response, quiet,
                  output_fmt=output_fmt, assert_status=assert_status,
                  assert_contains=assert_contains, assert_header=assert_header)

patch = _apply_options(patch, _REQ_OPTIONS)


@click.command()
@click.argument("url")
@click.pass_context
def delete(ctx, url, header, param, auth, timeout, no_verify, save, save_response, quiet, output_fmt,
           assert_status, assert_contains, assert_header):
    """Send DELETE request"""
    _send_request(ctx, "DELETE", url, None, header, param, auth, timeout, no_verify, save,
                  save_response, quiet, output_fmt=output_fmt,
                  assert_status=assert_status, assert_contains=assert_contains,
                  assert_header=assert_header)

delete = _apply_options(delete, _REQ_OPTIONS)

