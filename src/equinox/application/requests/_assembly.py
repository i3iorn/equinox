"""Qt-free request body assembly helpers.

These functions are pure Python with no GUI or Qt dependencies.  They live
in the application layer so that ``execution.py`` can import them without
triggering the GUI package chain (which would create a circular import).

``gui/request_panel/builder.py`` re-exports these symbols for backward
compatibility with existing call sites.
"""

from __future__ import annotations

import json as _json
import logging
from typing import TYPE_CHECKING, Any, Callable, Optional, Union
from uuid import uuid4

from equinox.versioning import get_app_version

if TYPE_CHECKING:
    from equinox.core.request import Request

logger = logging.getLogger(__name__)

# ── System default headers ────────────────────────────────────────────────────

_SYSTEM_DEFAULTS: dict[str, Union[str, Callable[[], str]]] = {
    "X-Request-ID": lambda: str(uuid4()),
    "User-Agent": "Equinox API testing v" + get_app_version(),
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate",
}


def apply_default_headers(request: Request) -> None:
    """Inject system-level default headers that the user has not set."""
    existing = {str(k).lower() for k in request.headers.keys()}
    for name, value_or_factory in _SYSTEM_DEFAULTS.items():
        if name.lower() not in existing:
            request.headers[name] = (
                value_or_factory() if callable(value_or_factory) else value_or_factory
            )


# ── Body-type → MIME content-type mapping ─────────────────────────────────────

_CONTENT_TYPE_MAP: dict[str, str] = {
    "raw (JSON)": "application/json",
    "raw (XML)": "application/xml",
    "form-urlencoded": "application/x-www-form-urlencoded",
    "GraphQL": "application/json",
}

# Security: maximum body size accepted (100 MB)
_MAX_BODY_SIZE = 100_000_000


# ── Body assembly ─────────────────────────────────────────────────────────────


def assemble_body(
    body_type: str,
    body_text: str,
    gql_query: str,
    gql_vars: str,
    multipart_rows: list[dict[str, str]],
) -> tuple[Optional[str], Optional[list[Any]]]:
    """Assemble request body from editor state.

    Returns ``(body, multipart_data)``.  Exactly one of the two will be set
    (or both None for ``body_type == "none"``).

    Raises:
        ValueError: If the body exceeds the maximum allowed size.
    """
    if body_type == "multipart/form-data":
        return None, [r for r in multipart_rows if r.get("key", "").strip()]

    if body_type == "GraphQL":
        return _assemble_graphql_body(gql_query, gql_vars), None

    if body_type == "none":
        return None, None

    if not body_text:
        return None, None

    if len(body_text) > _MAX_BODY_SIZE:
        logger.warning(
            "assemble_body: body rejected body_type=%s size=%d max=%d",
            body_type,
            len(body_text),
            _MAX_BODY_SIZE,
        )
        raise ValueError(f"Body exceeds maximum supported size ({_MAX_BODY_SIZE} bytes)")

    return body_text, None


def _assemble_graphql_body(query: str, variables_json: str) -> str:
    """Build a GraphQL request body JSON string."""
    gql_body: dict[str, Any] = {"query": query}
    if variables_json and variables_json.strip():
        try:
            gql_body["variables"] = _json.loads(variables_json)
        except (ValueError, _json.JSONDecodeError) as exc:
            logger.warning("graphql_vars_invalid: %s", exc)
            raise ValueError("GraphQL variables must be valid JSON") from exc
    return _json.dumps(gql_body)


def inject_content_type(
    body: str | None,
    body_type: str,
    headers: dict[str, str],
) -> dict[str, str]:
    """Return a new headers dict with Content-Type added when appropriate.

    No-op when body is empty, Content-Type is already set, or body_type
    has no known MIME mapping.  The input dict is never mutated.
    """
    if not body:
        return headers
    if any(k.lower() == "content-type" for k in headers):
        return headers
    ct = _CONTENT_TYPE_MAP.get(body_type)
    if ct is None:
        return headers
    return {**headers, "Content-Type": ct}
