from equinox.core.request import Response

from ._python_helpers import _inject_auth_into_headers
from .utils import _build_url_with_params, _escape_single_quoted


class PhpCurlGenerator:
    def generate(self, response: Response) -> str:
        request = response.request
        lines = ["<?php", "", "$ch = curl_init();", ""]

        url = _build_url_with_params(request.url, request.params or {})
        lines.append(f"curl_setopt($ch, CURLOPT_URL, '{_escape_single_quoted(url)}');")
        lines.append("curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);")
        lines.append(f"curl_setopt($ch, CURLOPT_CUSTOMREQUEST, '{request.method}');")

        headers = dict(request.headers or {})
        _inject_auth_into_headers(request, headers)
        if headers:
            h_list = [
                f"'{_escape_single_quoted(k)}: {_escape_single_quoted(v)}'"
                for k, v in headers.items()
            ]
            lines.append(f"$headers = [{', '.join(h_list)}];")
            lines.append("curl_setopt($ch, CURLOPT_HTTPHEADER, $headers);")

        if request.body:
            body_text = (
                request.body.decode("utf-8", errors="replace")
                if isinstance(request.body, bytes)
                else request.body
            )
            lines.append(
                f"curl_setopt($ch, CURLOPT_POSTFIELDS, '{_escape_single_quoted(body_text)}');"
            )

        lines.append("")
        lines.append("$response = curl_exec($ch);")
        lines.append("$status = curl_getinfo($ch, CURLINFO_HTTP_CODE);")
        lines.append("curl_close($ch);")
        lines.append("")
        lines.append('echo $status . "\\n";')
        lines.append("echo $response;")

        return "\n".join(lines)
