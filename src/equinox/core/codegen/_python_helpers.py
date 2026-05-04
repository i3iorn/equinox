import json
from equinox.core.request import Request
from .utils import _auth_type_name, _REDACTED_TOKEN, _REDACTED_KEY

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

def _auth_kwarg_for_basic(request: Request) -> str | None:
    from .utils import _REDACTED_USER, _REDACTED_PASS
    if request.auth and _auth_type_name(request.auth) == "BasicAuth":
        return f"auth=({_REDACTED_USER!r}, {_REDACTED_PASS!r})"
    return None

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
