import json
from typing import Union

from equinox.core.request import Request, Response

from ._python_helpers import _auth_kwarg_for_basic, _inject_auth_into_headers, _python_body_lines


class PythonRequestsGenerator:
    def generate(self, response_or_request: Union[Response, Request]) -> str:
        request = (
            response_or_request.request
            if isinstance(response_or_request, Response)
            else response_or_request
        )
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

        extra, body_arg = _python_body_lines(request)
        lines.extend(extra)

        args = [f"{request.url!r}"]
        if headers:
            args.append("headers=headers")
        if request.params:
            args.append("params=params")
        if body_arg:
            args.append(body_arg)
        if auth_kwarg:
            args.append(auth_kwarg)

        method = request.method.lower()
        lines.append(f"response = requests.{method}({', '.join(args)})")
        lines.append("")
        lines.append("print(response.status_code)")
        lines.append("print(response.text)")

        return "\n".join(lines)


class PythonHttpxGenerator:
    def generate(self, response_or_request: Union[Response, Request]) -> str:
        request = (
            response_or_request.request
            if isinstance(response_or_request, Response)
            else response_or_request
        )
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

        extra, body_arg = _python_body_lines(request)
        lines.extend(extra)

        args = [f"{request.url!r}"]
        if headers:
            args.append("headers=headers")
        if request.params:
            args.append("params=params")
        if body_arg:
            args.append(body_arg)
        if auth_kwarg:
            args.append(auth_kwarg)

        method = request.method.lower()
        lines.append("with httpx.Client() as client:")
        lines.append(f"    response = client.{method}({', '.join(args)})")
        lines.append("    print(response.status_code)")
        lines.append("    print(response.text)")

        return "\n".join(lines)
