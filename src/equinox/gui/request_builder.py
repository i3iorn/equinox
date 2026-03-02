"""Pure-logic helpers for assembling HTTP request parameters from GUI state.

These functions have no PyQt6 dependency so they can be unit-tested
without a display server.
"""

import json as _json
from typing import Any, Dict, List, Optional


def assemble_body(
    body_type: str,
    body_text: str,
    gql_query: str,
    gql_vars: str,
    multipart_rows: List[Dict[str, str]],
) -> tuple:
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
        gql_body: dict = {"query": gql_query}
        try:
            parsed = _json.loads(gql_vars) if gql_vars else None
            if parsed is not None:
                gql_body["variables"] = parsed
        except Exception:
            pass
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

    Returns a new headers dict; does not mutate the input.
    Multipart is excluded — httpx sets the boundary automatically.
    """
    if not body or "Content-Type" in headers:
        return headers
    ct_map = {
        "raw (JSON)": "application/json",
        "raw (XML)": "application/xml",
        "form-urlencoded": "application/x-www-form-urlencoded",
        "GraphQL": "application/json",
    }
    ct = ct_map.get(body_type)
    if ct:
        headers = dict(headers)
        headers["Content-Type"] = ct
    return headers
