from equinox.core.json_tools import safe_json_dumps
from equinox.core.request import Request
from equinox.core.request import Response

from ._python_helpers import _auth_kwarg_for_basic
from ._python_helpers import _inject_auth_into_headers
from ._python_helpers import _python_body_lines


class PythonGeneratorBase:
    """Shared code-generation logic for Python HTTP clients."""

    def __init__(self, module_name: str, method_prefix: str = "") -> None:
        self.module_name = module_name
        self.method_prefix = method_prefix

    def _build_request_lines(
        self,
        request: Request,
        headers: dict[str, str],
        auth_kwarg: str | None,
    ) -> list[str]:
        args = [f"{request.url!r}"]
        if headers:
            args.append("headers=headers")
        if request.params:
            args.append("params=params")
        extra, body_arg = _python_body_lines(request)
        if body_arg:
            args.append(body_arg)
        if auth_kwarg:
            args.append(auth_kwarg)
        lines: list[str] = []
        if self.method_prefix:
            lines.append(f"{self.method_prefix}.{request.method.lower()}({', '.join(args)})")
        else:
            lines.append(f"{request.method.lower()}({', '.join(args)})")
        return lines

    def generate(self, response_or_request: Response | Request) -> str:
        request = (
            response_or_request.request
            if isinstance(response_or_request, Response)
            else response_or_request
        )
        raise NotImplementedError("Subclasses must implement generate")


class PythonRequestsGenerator(PythonGeneratorBase):
    def __init__(self) -> None:
        super().__init__("requests")

    def generate(self, response_or_request: Response | Request) -> str:
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
            lines.append(f"headers = {safe_json_dumps(headers, indent=4)}")
            lines.append("")
        if request.params:
            lines.append(f"params = {safe_json_dumps(request.params, indent=4)}")
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


class PythonHttpxGenerator(PythonGeneratorBase):
    def __init__(self) -> None:
        super().__init__("httpx")

    def generate(self, response_or_request: Response | Request) -> str:
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
            lines.append(f"headers = {safe_json_dumps(headers, indent=4)}")
            lines.append("")
        if request.params:
            lines.append(f"params = {safe_json_dumps(request.params, indent=4)}")
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
