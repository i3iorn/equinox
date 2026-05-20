from typing import Union

from equinox.core.request import Request, Response

from ._python_helpers import _inject_auth_into_headers
from .utils import _escape_go_string


class GoHttpGenerator:
    def generate(self, response_or_request: Union[Response, Request]) -> str:
        request = (
            response_or_request.request
            if isinstance(response_or_request, Response)
            else response_or_request
        )
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
        lines.append('    fmt.Println("Status:", resp.Status)')
        lines.append('    fmt.Println("Headers:", resp.Header)')
        lines.append("}")
        return "\n".join(lines)
