"""Pure-logic helpers for assembling HTTP request parameters from GUI state.

These functions have no PyQt6 dependency so they can be unit-tested
without a display server.
"""

import json as _json
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

__all__ = ["assemble_body", "inject_content_type", "detect_body_type"]

# Canonical mapping from GUI body-type labels to MIME Content-Type values.
# Used by both inject_content_type (forward) and detect_body_type (via header sniff).
# Multipart is intentionally absent — httpx sets the boundary automatically.
_CONTENT_TYPE_MAP: Dict[str, str] = {
    "raw (JSON)":       "application/json",
    "raw (XML)":        "application/xml",
    "form-urlencoded":  "application/x-www-form-urlencoded",
    "GraphQL":          "application/json",
}


def assemble_body(
    body_type: str,
    body_text: str,
    gql_query: str,
    gql_vars: str,
    multipart_rows: List[Dict[str, str]],
) -> Tuple[Optional[str], Optional[List[Any]]]:
    """Return ``(body, multipart_data)`` from editor state.

    Args:
        body_type: Selected value from the body-type combo box.
        body_text: Raw text from the body editor (already stripped by caller).
        gql_query: GraphQL query string.
        gql_vars: GraphQL variables JSON string.
        multipart_rows: Rows from the multipart table as dicts.

    Returns:
        ``(body: Optional[str], multipart_data: Optional[list])``
    """
    body: Optional[str] = None
    multipart_data: Optional[List[Any]] = None

    if body_type == "multipart/form-data":
        multipart_data = [r for r in multipart_rows if r.get("key", "").strip()]
    elif body_type == "GraphQL":
        gql_body: Dict[str, Any] = {"query": gql_query}
        try:
            parsed = _json.loads(gql_vars) if gql_vars else None
            if parsed is not None:
                gql_body["variables"] = parsed
        except Exception:
            logger.warning("Failed to parse GraphQL variables as JSON", exc_info=True)
        body = _json.dumps(gql_body)
    else:
        body = body_text or None

    return body, multipart_data


def inject_content_type(
    body: Optional[str],
    body_type: str,
    headers: Dict[str, str],
) -> Dict[str, str]:
    """Add a ``Content-Type`` header when *body* is present and none is set.

    The lookup is **case-insensitive** — if the caller already supplied
    ``content-type`` or ``CONTENT-TYPE``, no duplicate is added.

    Returns a new headers dict; does not mutate the input.
    Multipart is excluded — httpx sets the boundary automatically.
    """
    if not body:
        return headers
    # Case-insensitive check: HTTP headers are case-insensitive by spec.
    if any(k.lower() == "content-type" for k in headers):
        return headers
    ct = _CONTENT_TYPE_MAP.get(body_type)
    if ct:
        headers = dict(headers)
        headers["Content-Type"] = ct
    return headers


def detect_body_type(body: str, headers: Optional[Dict] = None) -> str:
    """Guess body type from content or Content-Type header.

    The ``Content-Type`` header lookup is **case-insensitive**.

    Pure-logic helper — no Qt dependency.
    """
    # Case-insensitive Content-Type lookup.
    ct = next(
        (v for k, v in (headers or {}).items() if k.lower() == "content-type"),
        "",
    ).lower()

    if "json" in ct:
        return "raw (JSON)"
    if "xml" in ct:
        return "raw (XML)"
    if "urlencoded" in ct:
        return "form-urlencoded"
    if "text" in ct:
        return "raw (text)"

    # Sniff content when no definitive header is present.
    stripped = body.strip()
    if stripped.startswith(("{", "[")):
        try:
            _json.loads(stripped)
            return "raw (JSON)"
        except Exception:
            pass  # Not valid JSON — fall through to other heuristics
    if stripped.startswith("<") and (">" in stripped):
        return "raw (XML)"
    # form-urlencoded: one or more key=value pairs (& separated for multiple).
    # Require that the key portion (before the first =) contains no whitespace,
    # which excludes plain sentences that happen to contain "=".
    if "=" in stripped:
        key_part = stripped.split("=", 1)[0]
        if key_part and not any(c in key_part for c in " \t\n\r"):
            return "form-urlencoded"
    return "raw (text)"
