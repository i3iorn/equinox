import json
from equinox.core.request import Response
from .utils import _build_url_with_params, _escape_single_quoted
from ._python_helpers import _inject_auth_into_headers

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
