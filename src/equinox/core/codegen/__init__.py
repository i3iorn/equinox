import logging

from equinox.core.request import Request, Response
from equinox.security import redact_url

from .curl import CurlGenerator
from .go import GoHttpGenerator
from .har import HARGenerator
from .javascript import JavaScriptFetchGenerator
from .php import PhpCurlGenerator
from .python import PythonHttpxGenerator, PythonRequestsGenerator
from .ruby import RubyNetHttpGenerator

logger = logging.getLogger(__name__)

_GENERATORS = {
    "Python (requests)": PythonRequestsGenerator,
    "Python (httpx)": PythonHttpxGenerator,
    "JavaScript (fetch)": JavaScriptFetchGenerator,
    "Go": GoHttpGenerator,
    "Ruby": RubyNetHttpGenerator,
    "PHP (cURL)": PhpCurlGenerator,
    "cURL": CurlGenerator,
    "HAR": HARGenerator,
}

GENERATORS = _GENERATORS


def generate_code(fmt: str, request_or_response: Request | Response) -> str:
    """Generate client code for the given response/request."""
    gen_cls = _GENERATORS.get(fmt)
    if not gen_cls:
        raise KeyError(fmt)

    if isinstance(request_or_response, Response):
        response = request_or_response
    else:
        # Compatibility: wrap Request in a dummy Response
        from datetime import datetime

        response = Response(
            request=request_or_response,
            status_code=200,
            reason="OK",
            headers={},
            body=b"",
            elapsed=0.0,
            timestamp=datetime.now(),
        )

    logger.debug(
        "generate_code: format=%r method=%s url=%s status=%s",
        fmt,
        response.request.method,
        redact_url(response.request.url) if response.request.url else "",
        response.status_code,
    )
    gen_cls = _GENERATORS.get(fmt)
    if not gen_cls:
        return f"Error: Unsupported format '{fmt}'"
    return gen_cls().generate(response)


__all__ = ["generate_code"]
