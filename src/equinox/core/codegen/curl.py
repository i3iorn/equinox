import shlex
from typing import Union

from equinox.core.request import Request, Response

from ._python_helpers import _inject_auth_into_headers
from .utils import _build_url_with_params


class CurlGenerator:
    def generate(self, response_or_request: Union[Response, Request]) -> str:
        request = (
            response_or_request.request
            if isinstance(response_or_request, Response)
            else response_or_request
        )
        url = _build_url_with_params(request.url, request.params or {})

        parts = ["curl", "-X", request.method]

        headers = dict(request.headers or {})
        _inject_auth_into_headers(request, headers)
        for k, v in headers.items():
            parts.extend(["-H", f"{k}: {v}"])

        if request.body:
            parts.extend(["-d", request.body])

        parts.append(url)

        return " ".join(shlex.quote(p) for p in parts)
