"""Request history CLI commands."""

import json
import sys
from pathlib import Path

import click

from equinox.storage import HistoryManager


@click.group()
def history():
    """View and export request history"""
    pass


_METHOD_COLOR = {
    "GET": "green", "POST": "yellow", "PUT": "blue",
    "PATCH": "magenta", "DELETE": "red", "HEAD": "white", "OPTIONS": "white",
}


def _status_color(entry: dict) -> str:
    """Return the terminal colour for a history entry based on its outcome."""
    status = entry.get("status_code")
    if entry.get("error"):
        return "red"
    if isinstance(status, int) and status < 400:
        return "green"
    if isinstance(status, int) and status >= 400:
        return "red"
    return "yellow"


def _print_history_entry(entry: dict) -> None:
    """Print a single history entry to the terminal."""
    status = entry.get("status_code")
    elapsed = entry.get("elapsed")
    method = entry["method"]

    color = _status_color(entry)
    method_str = click.style(f"{method:<7}", fg=_METHOD_COLOR.get(method, "white"), bold=True)
    id_str = click.style(f"[{entry['id']}]", fg=color)
    url_str = click.style(entry["url"], fg=color)
    click.echo(f"{id_str} {method_str} {url_str}")

    if status is not None:
        elapsed_str = f" | Time: {elapsed:.3f}s" if elapsed else ""
        click.echo(f"    Status: {status}{elapsed_str}")
    if entry.get("error"):
        click.echo(f"    Error: {entry['error']}")
    click.echo(f"    Executed: {entry['executed_at']}")
    click.echo()


@history.command("list")
@click.option("--limit", "-n", type=int, default=20, help="Number of entries to show")
def history_list(limit):
    """List request history"""
    from equinox.cli.main import get_db
    db = get_db()
    manager = HistoryManager(db)
    entries = manager.list_history(limit=limit)

    if not entries:
        click.echo("No history found")
        return

    for entry in entries:
        _print_history_entry(entry)


@history.command("export")
@click.option("--format", "-f", "fmt", type=click.Choice(["json", "csv", "har"]),
              default="json", help="Export format: json, csv, or har")
@click.option("--output", "-o", type=click.Path(), required=True, help="Output file path")
@click.option("--limit", "-n", type=int, default=1000, help="Maximum entries to export")
def history_export(fmt, output, limit):
    """Export request history to JSON, CSV, or HAR format.

    \b
    Examples:
      equinox history export --format json -o history.json
      equinox history export --format har  -o session.har
      equinox history export --format csv  -o history.csv --limit 50
    """
    import csv

    from equinox.cli.main import get_db
    db = get_db()
    manager = HistoryManager(db)
    entries = manager.list_history(limit=limit)

    if not entries:
        click.echo("No history to export")
        return

    out_path = Path(output)

    try:
        if fmt == "json":
            with out_path.open("w", encoding="utf-8") as f:
                f.write("[\n")
                for index, entry in enumerate(entries):
                    row = {k: str(v) if not isinstance(v, (str, int, float, bool, type(None))) else v
                           for k, v in entry.items()}
                    prefix = "  " if index == 0 else ", "
                    f.write(prefix + json.dumps(row) + "\n")
                f.write("]\n")

        elif fmt == "csv":
            fieldnames = list(entries[0].keys())
            with out_path.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                for entry in entries:
                    writer.writerow(entry)

        elif fmt == "har":
            _export_history_har(entries, out_path)

        click.echo(f"✓ Exported {len(entries)} history entries to {out_path} ({fmt})")
    except Exception as exc:
        click.secho(f"✗ Export failed: {exc}", fg="red", err=True)
        raise click.Exit(1)


def _export_history_har(entries: list, out_path: Path) -> None:
    """Write history entries as an HTTP Archive (HAR 1.2) file."""
    import datetime

    har_entries = []
    for entry in entries:
        req_headers = entry.get("request_headers") or {}
        resp_headers = entry.get("response_headers") or {}
        if isinstance(req_headers, str):
            try:
                req_headers = json.loads(req_headers)
            except Exception:
                req_headers = {}
        if isinstance(resp_headers, str):
            try:
                resp_headers = json.loads(resp_headers)
            except Exception:
                resp_headers = {}

        har_req_headers = [{"name": k, "value": str(v)} for k, v in req_headers.items()]
        har_resp_headers = [{"name": k, "value": str(v)} for k, v in resp_headers.items()]
        body_text = entry.get("response_body") or ""
        content_type = resp_headers.get("content-type", "text/plain")
        elapsed_ms = int((entry.get("elapsed") or 0) * 1000)
        executed_at = entry.get("executed_at", "")
        # Normalise to ISO-8601 with timezone suffix required by HAR spec
        if executed_at and "T" not in executed_at:
            executed_at = executed_at.replace(" ", "T") + "Z"
        elif executed_at and not executed_at.endswith("Z") and "+" not in executed_at:
            executed_at += "Z"

        har_entries.append({
            "startedDateTime": executed_at,
            "time": elapsed_ms,
            "request": {
                "method": entry.get("method", "GET"),
                "url": entry.get("url", ""),
                "httpVersion": "HTTP/1.1",
                "headers": har_req_headers,
                "queryString": [],
                "cookies": [],
                "headersSize": -1,
                "bodySize": len((entry.get("request_body") or "").encode("utf-8")),
                "postData": {
                    "mimeType": req_headers.get("content-type", "text/plain"),
                    "text": entry.get("request_body") or "",
                } if entry.get("request_body") else None,
            },
            "response": {
                "status": entry.get("status_code") or 0,
                "statusText": entry.get("reason") or "",
                "httpVersion": "HTTP/1.1",
                "headers": har_resp_headers,
                "cookies": [],
                "content": {
                    "size": len(body_text.encode("utf-8")),
                    "mimeType": content_type,
                    "text": body_text,
                },
                "redirectURL": "",
                "headersSize": -1,
                "bodySize": len(body_text.encode("utf-8")),
            },
            "cache": {},
            "timings": {"send": 0, "wait": elapsed_ms, "receive": 0},
        })

    # Remove None postData fields
    for e in har_entries:
        if e["request"].get("postData") is None:
            del e["request"]["postData"]

    har = {
        "log": {
            "version": "1.2",
            "creator": {"name": "Equinox", "version": "1.0"},
            "entries": har_entries,
        }
    }
    out_path.write_text(json.dumps(har, indent=2, ensure_ascii=False), encoding="utf-8")


# ── history search ────────────────────────────────────────────────────────────

def _parse_status(raw: str):
    """Return (status_code, status_class) from a user string like '200', '2xx', or 'errors'."""
    if not raw:
        return None, ""
    normalised = raw.strip().lower()
    if normalised in ("2xx", "3xx", "4xx", "5xx", "errors"):
        return None, normalised
    try:
        return int(raw), ""
    except ValueError:
        raise click.BadParameter(
            f"Invalid status filter '{raw}'. Use an integer (200), "
            "a class (2xx/3xx/4xx/5xx), or 'errors'."
        )


@history.command("search")
@click.option("--query", "-q", default="", help="Text search in URL or request body")
@click.option("--status", "-s", "status_raw", default="",
              help="Status filter: exact code (200), class (2xx/4xx/5xx), or 'errors'")
@click.option("--method", "-m", default="",
              type=click.Choice(["", "GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
                                case_sensitive=False),
              help="HTTP method filter")
@click.option("--body-regex", "-r", default="",
              help="Regex pattern to match in response body")
@click.option("--jsonpath", "-j", default="",
              help="JSONPath expression that must match in the response body")
@click.option("--jsonpath-value", default=None,
              help="Expected value at the JSONPath (requires --jsonpath)")
@click.option("--content-type", "-c", default="",
              help="Substring match in response Content-Type (e.g. 'json', 'text/html')")
@click.option("--header", default="",
              help="Response header filter as 'Name: value' (substring match)")
@click.option("--min-time", type=float, default=None,
              help="Minimum response time in seconds")
@click.option("--max-time", type=float, default=None,
              help="Maximum response time in seconds")
@click.option("--after", default=None,
              help="Only entries executed after this ISO-8601 timestamp")
@click.option("--before", default=None,
              help="Only entries executed before this ISO-8601 timestamp")
@click.option("--limit", "-n", type=int, default=20, help="Number of entries to show")
def history_search(
    query, status_raw, method, body_regex, jsonpath, jsonpath_value,
    content_type, header, min_time, max_time, after, before, limit,
):
    """Search and filter request history.

    \b
    Examples:
      equinox history search --status 404
      equinox history search --status 2xx --method GET
      equinox history search --body-regex "error.*timeout"
      equinox history search --jsonpath "$.data[*].id"
      equinox history search --jsonpath "$.status" --jsonpath-value "ok"
      equinox history search --content-type json --min-time 1.0
      equinox history search --header "X-Request-Id"
      equinox history search --after 2025-01-01 --before 2025-06-01
    """
    from equinox.cli.main import get_db

    status_code, status_class = _parse_status(status_raw)

    db = get_db()
    manager = HistoryManager(db)

    try:
        entries = manager.search_history(
            query=query,
            method=method,
            status_class=status_class,
            status_code=status_code,
            body_regex=body_regex,
            jsonpath=jsonpath,
            jsonpath_value=jsonpath_value,
            content_type=content_type,
            header=header,
            min_elapsed=min_time,
            max_elapsed=max_time,
            executed_after=after,
            executed_before=before,
            limit=limit,
        )
    except Exception as exc:
        click.secho(f"✗ Search failed: {exc}", fg="red", err=True)
        raise SystemExit(1)

    if not entries:
        click.echo("No matching history entries found")
        return

    click.echo(f"Found {len(entries)} matching entries:\n")
    for entry in entries:
        _print_history_entry(entry)
