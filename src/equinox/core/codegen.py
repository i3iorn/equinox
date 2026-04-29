"""
Code generation — convert a Response object (and its embedded Request)
into client code in various languages.
"""

import json
import logging
from typing import Optional
from urllib.parse import urlencode

from equinox.core.request import Request, Response
from equinox.core.security_policy import redact_url

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _auth_type_name(auth) -> str:
    return type(auth).__name__ if auth else ""


_REDACTED_TOKEN = "<YOUR_TOKEN>"
_REDACTED_USER = "<YOUR_USERNAME>"
_REDACTED_PASS = "<YOUR_PASSWORD>"
_REDACTED_KEY = "<YOUR_API_KEY>"


def _escape_go_string(s: str) -> str:
    return (
        s.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )


def _escape_single_quoted(s: str) -> str:
    return s.replace("\\", "\\\\").replace("'", "\\\\'")


_escape_ruby_single = _escape_single_quoted
_escape_php_single = _escape_single_quoted


def _inject_auth_into_headers(request: Request, headers: dict) -> None:
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
    if request.auth and _auth_type_name(request.auth) == "BasicAuth":
        return f"auth=({_REDACTED_USER!r}, {_REDACTED_PASS!r})"
    return None


def _build_url_with_params(url: str, params: dict) -> str:
    if not params:
        return url
    qs = urlencode(params)
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}{qs}"


def _python_body_lines(request: Request) -> tuple:
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


# ─────────────────────────────────────────────────────────────────────────────
# Generators
# ─────────────────────────────────────────────────────────────────────────────

class PythonRequestsGenerator:
    def generate(self, response: Response) -> str:
        request = response.request
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
        lines.append("")
        lines.append("# Response details")
        lines.append("print(response.status_code)")
        lines.append("print(response.headers)")
        lines.append("print(response.text)")
        return "\n".join(lines)


class PythonHttpxGenerator:
    def generate(self, response: Response) -> str:
        request = response.request
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
        lines.append("    print(response.headers)")
        lines.append("    print(response.text)")
        return "\n".join(lines)


class JavaScriptFetchGenerator:
    def generate(self, response: Response) -> str:
        request = response.request
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
        lines.append("console.log(response.status);")
        lines.append("console.log(Object.fromEntries(response.headers.entries()));")
        lines.append("console.log(await response.text());")
        return "\n".join(lines)


class GoHttpGenerator:
    def generate(self, response: Response) -> str:
        request = response.request
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
        lines.append("    fmt.Println(\"Status:\", resp.Status)")
        lines.append("    fmt.Println(\"Headers:\", resp.Header)")
        lines.append("}")
        return "\n".join(lines)


class RubyNetHttpGenerator:
    def generate(self, response: Response) -> str:
        request = response.request
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
        lines.append("puts response.to_hash")
        lines.append("puts response.body")
        return "\n".join(lines)


class PhpCurlGenerator:
    def generate(self, response: Response) -> str:
        request = response.request
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
    def generate(self, response: Response) -> str:
        request = response.request
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
            safe_body = body.replace("'", "'\\''")
            parts.append(f"--data '{safe_body}'")

        parts.append("\n# Expected response:")
        parts.append(f"# Status: {response.status_code}")
        parts.append(f"# Headers: {dict(response.headers)}")
        parts.append(f"# Body (truncated): {response.text[:200]!r}")

        return " \\\n  ".join(parts)


class HARGenerator:
    def generate(self, response: Response) -> str:
        request = response.request

        har = {
            "log": {
                "version": "1.2",
                "creator": {"name": "Equinox", "version": "2.0"},
                "entries": [
                    {
                        "startedDateTime": response.timestamp.isoformat(),
                        "time": response.elapsed * 1000,
                        "request": {
                            "method": request.method,
                            "url": response.sent_url or request.url,
                            "httpVersion": "HTTP/1.1",
                            "headers": [{"name": k, "value": v} for k, v in (request.headers or {}).items()],
                            "queryString": [{"name": k, "value": v} for k, v in (request.params or {}).items()],
                            "postData": {
                                "mimeType": request.headers.get("Content-Type", ""),
                                "text": request.body or "",
                            },
                        },
                        "response": {
                            "status": response.status_code,
                            "statusText": response.reason,
                            "httpVersion": "HTTP/1.1",
                            "headers": [{"name": k, "value": v} for k, v in response.headers.items()],
                            "content": {
                                "size": response.size,
                                "mimeType": response.content_type or "",
                                "text": response.text,
                            },
                            "redirectURL": "",
                            "headersSize": -1,
                            "bodySize": response.size,
                        },
                        "timings": response.timings or {},
                    }
                ],
            }
        }
        return json.dumps(har, indent=4)


# ─────────────────────────────────────────────────────────────────────────────
# Registry
# ─────────────────────────────────────────────────────────────────────────────

GENERATORS: dict = {
    "Python (requests)": PythonRequestsGenerator,
    "Python (httpx)": PythonHttpxGenerator,
    "JavaScript (fetch)": JavaScriptFetchGenerator,
    "Go": GoHttpGenerator,
    "Ruby": RubyNetHttpGenerator,
    "PHP (cURL)": PhpCurlGenerator,
    "cURL": CurlGenerator,
    "HAR": HARGenerator,
}


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def generate_code(fmt: str, response: Response) -> str:
    logger.debug(
        "generate_code: format=%r method=%s url=%s status=%s",
        fmt,
        response.request.method,
        redact_url(response.request.url) if response.request.url else "",
        response.status_code,
    )
    cls = GENERATORS[fmt]
    return cls().generate(response)
