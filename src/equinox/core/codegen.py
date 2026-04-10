"""Code generation — convert a Request object to client code in various languages."""

import json
import logging
from typing import Optional
from urllib.parse import urlencode

from equinox.core.request import Request

logger = logging.getLogger(__name__)


def _auth_type_name(auth) -> str:
    return type(auth).__name__ if auth else ""


_REDACTED_TOKEN = "<YOUR_TOKEN>"
_REDACTED_USER = "<YOUR_USERNAME>"
_REDACTED_PASS = "<YOUR_PASSWORD>"
_REDACTED_KEY = "<YOUR_API_KEY>"


def _escape_go_string(s: str) -> str:
    """Escape a string for use inside Go double-quoted string literals."""
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")


def _escape_single_quoted(s: str) -> str:
    """Escape a string for use inside single-quoted string literals (Ruby/PHP)."""
    return s.replace("\\", "\\\\").replace("'", "\\\\'")


# Backward-compatible aliases so existing callers are not broken.
_escape_ruby_single = _escape_single_quoted
_escape_php_single = _escape_single_quoted


def _inject_auth_into_headers(request: Request, headers: dict) -> None:
    """Inject auth credentials into *headers* as redacted placeholders.

    Handles Bearer, Basic (as header), and API-Key (header location).
    Shared by all code generators to avoid duplication.
    """
    if not request.auth:
        return
    name = _auth_type_name(request.auth)
    auth = request.auth
    if name == "BearerAuth":
        headers["Authorization"] = f"Bearer {_REDACTED_TOKEN}"
    elif name == "BasicAuth":
        headers["Authorization"] = f"Basic {_REDACTED_TOKEN}"
    elif name == "APIKeyAuth" and getattr(auth, "location", "header") == "header":
        headers[auth.key] = _REDACTED_KEY


def _auth_kwarg_for_basic(request: Request) -> Optional[str]:
    """Return an ``auth=(…)`` keyword string for Basic auth, or None."""
    if request.auth and _auth_type_name(request.auth) == "BasicAuth":
        return f"auth=({_REDACTED_USER!r}, {_REDACTED_PASS!r})"
    return None


def _build_url_with_params(url: str, params: dict) -> str:
    """Append URL-encoded query params to *url*."""
    if not params:
        return url
    qs = urlencode(params)
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}{qs}"


def _python_body_lines(request: Request) -> tuple:
    """Return (extra_lines, body_arg) for Python-based generators."""
    extra: list = []
    body_arg = ""
    if request.body:
        try:
            parsed = json.loads(request.body)
            extra.append(f"json_body = {json.dumps(parsed, indent=4)}")
            extra.append("")
            body_arg = "json=json_body"
        except (json.JSONDecodeError, ValueError):
            extra.append(f"body = {request.body!r}")
            extra.append("")
            body_arg = "data=body"
    return extra, body_arg


class PythonRequestsGenerator:
    """Generate Python code using the ``requests`` library."""

    def generate(self, request: Request) -> str:
        lines = ["import requests", ""]

        headers = dict(request.headers or {})
        _inject_auth_into_headers(request, headers)
        auth_kwarg = _auth_kwarg_for_basic(request)

        if headers:
            lines.append(f"headers = {json.dumps(headers, indent=4)}")
            lines.append("")

        if request.params:
            lines.append(f"params = {json.dumps(request.params, indent=4)}")
            lines.append("")

        body_lines, body_arg = _python_body_lines(request)
        lines.extend(body_lines)

        method = request.method.lower()
        args = [f'"{request.url}"']
        if headers:
            args.append("headers=headers")
        if request.params:
            args.append("params=params")
        if body_arg:
            args.append(body_arg)
        if auth_kwarg:
            args.append(auth_kwarg)

        args_str = ", ".join(args)
        lines.append(f"response = requests.{method}({args_str})")
        lines.append("print(response.status_code)")
        lines.append("print(response.text)")
        return "\n".join(lines)


class PythonHttpxGenerator:
    """Generate Python code using the ``httpx`` library."""

    def generate(self, request: Request) -> str:
        lines = ["import httpx", ""]

        headers = dict(request.headers or {})
        _inject_auth_into_headers(request, headers)
        auth_kwarg = _auth_kwarg_for_basic(request)

        if headers:
            lines.append(f"headers = {json.dumps(headers, indent=4)}")
            lines.append("")

        if request.params:
            lines.append(f"params = {json.dumps(request.params, indent=4)}")
            lines.append("")

        body_lines, body_arg = _python_body_lines(request)
        lines.extend(body_lines)

        method = request.method.lower()
        args = [f'"{request.url}"']
        if headers:
            args.append("headers=headers")
        if request.params:
            args.append("params=params")
        if body_arg:
            args.append(body_arg)
        if auth_kwarg:
            args.append(auth_kwarg)

        args_str = ", ".join(args)
        lines.append("with httpx.Client() as client:")
        lines.append(f"    response = client.{method}({args_str})")
        lines.append("    print(response.status_code)")
        lines.append("    print(response.text)")
        return "\n".join(lines)


class JavaScriptFetchGenerator:
    """Generate JavaScript code using the Fetch API."""

    def generate(self, request: Request) -> str:
        lines = []

        headers = dict(request.headers or {})
        _inject_auth_into_headers(request, headers)

        url = _build_url_with_params(request.url, request.params or {})

        body_line = None
        if request.body:
            try:
                parsed = json.loads(request.body)
                lines.append(f"const jsonBody = {json.dumps(parsed, indent=2)};")
                lines.append("")
                body_line = "body: JSON.stringify(jsonBody),"
            except (json.JSONDecodeError, ValueError):
                lines.append(f"const body = {request.body!r};")
                lines.append("")
                body_line = "body: body,"

        lines.append("const response = await fetch(")
        lines.append(f'  "{url}",')
        lines.append("  {")
        lines.append(f'    method: "{request.method}",')
        if headers:
            header_json = json.dumps(headers, indent=4)
            indented = header_json.replace("\n", "\n    ")
            lines.append(f"    headers: {indented},")
        if body_line:
            lines.append(f"    {body_line}")
        lines.append("  }")
        lines.append(");")
        lines.append("")
        lines.append("const data = await response.json();")
        lines.append("console.log(response.status, data);")
        return "\n".join(lines)


class GoHttpGenerator:
    """Generate Go code using the standard ``net/http`` package."""

    def generate(self, request: Request) -> str:
        lines = [
            "package main",
            "",
            "import (",
            '    "fmt"',
            '    "net/http"',
        ]

        if request.body:
            lines.append('    "strings"')
        lines.append(")")
        lines.append("")
        lines.append("func main() {")

        if request.body:
            safe = _escape_go_string(request.body)
            lines.append(f'    body := strings.NewReader("{safe}")')
            lines.append("    req, _ := http.NewRequest(")
            lines.append(f'        "{request.method}",')
            lines.append(f'        "{_escape_go_string(request.url)}",')
            lines.append("        body,")
            lines.append("    )")
        else:
            lines.append("    req, _ := http.NewRequest(")
            lines.append(f'        "{request.method}",')
            lines.append(f'        "{_escape_go_string(request.url)}",')
            lines.append("        nil,")
            lines.append("    )")

        lines.append("")

        headers = dict(request.headers or {})
        _inject_auth_into_headers(request, headers)
        for k, v in headers.items():
            lines.append(f'    req.Header.Set("{_escape_go_string(k)}", "{_escape_go_string(v)}")')
        if headers:
            lines.append("")

        lines.append("    resp, _ := http.DefaultClient.Do(req)")
        lines.append("    defer resp.Body.Close()")
        lines.append("    fmt.Println(resp.Status)")
        lines.append("}")
        return "\n".join(lines)


class RubyNetHttpGenerator:
    """Generate Ruby code using the standard ``net/http`` library."""

    def generate(self, request: Request) -> str:
        lines = [
            "require 'net/http'",
            "require 'uri'",
            "require 'json'",
            "",
        ]

        url = _build_url_with_params(request.url, request.params or {})

        lines.append(f"uri = URI('{_escape_single_quoted(url)}')")
        lines.append("http = Net::HTTP.new(uri.host, uri.port)")
        lines.append("http.use_ssl = uri.scheme == 'https'")
        lines.append("")

        method_class = {
            "GET": "Net::HTTP::Get", "POST": "Net::HTTP::Post",
            "PUT": "Net::HTTP::Put", "PATCH": "Net::HTTP::Patch",
            "DELETE": "Net::HTTP::Delete", "HEAD": "Net::HTTP::Head",
        }.get(request.method, f"Net::HTTP::{request.method.capitalize()}")

        lines.append(f"request = {method_class}.new(uri)")

        headers = dict(request.headers or {})
        _inject_auth_into_headers(request, headers)
        for k, v in headers.items():
            lines.append(f"request['{_escape_single_quoted(k)}'] = '{_escape_single_quoted(v)}'")

        if request.body:
            try:
                parsed = json.loads(request.body)
                lines.append(f"request.body = {json.dumps(parsed)}.to_json")
                if "Content-Type" not in headers:
                    lines.append("request['Content-Type'] = 'application/json'")
            except (json.JSONDecodeError, ValueError):
                lines.append(f"request.body = {request.body!r}")

        lines.append("")
        lines.append("response = http.request(request)")
        lines.append("puts response.code")
        lines.append("puts response.body")
        return "\n".join(lines)


class PhpCurlGenerator:
    """Generate PHP code using the cURL extension."""

    def generate(self, request: Request) -> str:
        lines = ["<?php", ""]

        url = _build_url_with_params(request.url, request.params or {})

        lines.append(f"$url = '{_escape_single_quoted(url)}';")
        lines.append("$ch = curl_init($url);")
        lines.append("")
        lines.append(f"curl_setopt($ch, CURLOPT_CUSTOMREQUEST, '{_escape_single_quoted(request.method)}');")
        lines.append("curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);")

        if not getattr(request, "verify_ssl", True):
            lines.append("curl_setopt($ch, CURLOPT_SSL_VERIFYPEER, false);")

        headers = dict(request.headers or {})
        _inject_auth_into_headers(request, headers)
        if headers:
            header_list = [f"'{_escape_single_quoted(k)}: {_escape_single_quoted(v)}'" for k, v in headers.items()]
            lines.append(f"curl_setopt($ch, CURLOPT_HTTPHEADER, [{', '.join(header_list)}]);")

        if request.body:
            safe = _escape_single_quoted(request.body)
            lines.append(f"curl_setopt($ch, CURLOPT_POSTFIELDS, '{safe}');")

        lines.append("")
        lines.append("$response = curl_exec($ch);")
        lines.append("$status   = curl_getinfo($ch, CURLINFO_HTTP_CODE);")
        lines.append("curl_close($ch);")
        lines.append("")
        lines.append("echo $status . \"\\n\";")
        lines.append("echo $response . \"\\n\";")
        return "\n".join(lines)


class CurlGenerator:
    """Generate a ``curl`` command-line invocation."""

    def generate(self, request: Request) -> str:
        parts: list = ["curl"]

        method = request.method.upper()
        if method != "GET":
            parts.append(f"-X {method}")

        url = _build_url_with_params(request.url, request.params or {})
        parts.append(f'"{url}"')

        headers = dict(request.headers or {})
        _inject_auth_into_headers(request, headers)
        for k, v in headers.items():
            safe_k = k.replace('"', '\\"')
            safe_v = str(v).replace('"', '\\"')
            parts.append(f'-H "{safe_k}: {safe_v}"')

        if request.body:
            body = (
                request.body
                if isinstance(request.body, str)
                else request.body.decode("utf-8", errors="replace")
            )
            # Single-quote escaping: ' → '\''
            safe_body = body.replace("'", "'\\''")
            parts.append(f"--data '{safe_body}'")

        return " \\\n  ".join(parts)


GENERATORS: dict = {
    "Python (requests)": PythonRequestsGenerator,
    "Python (httpx)": PythonHttpxGenerator,
    "JavaScript (fetch)": JavaScriptFetchGenerator,
    "Go": GoHttpGenerator,
    "Ruby": RubyNetHttpGenerator,
    "PHP (cURL)": PhpCurlGenerator,
    "cURL": CurlGenerator,
}


def generate_code(fmt: str, request: Request) -> str:
    """Generate client code for *request* in the given format.

    Args:
        fmt: One of the keys in :data:`GENERATORS`.
        request: The request to generate code for.

    Returns:
        Generated code as a string.

    Raises:
        KeyError: If *fmt* is not a known format.
    """
    logger.debug("generate_code: format=%r method=%s url=%s", fmt, request.method, request.url)
    cls = GENERATORS[fmt]
    return cls().generate(request)
