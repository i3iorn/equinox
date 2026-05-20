import json
from typing import Union

from equinox.core.request import Request, Response

from ._python_helpers import _inject_auth_into_headers
from .utils import _build_url_with_params


class JavaScriptFetchGenerator:
    def generate(self, response_or_request: Union[Response, Request]) -> str:
        request = (
            response_or_request.request
            if isinstance(response_or_request, Response)
            else response_or_request
        )
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
