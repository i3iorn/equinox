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

# Reverse mapping: MIME-type substring → GUI body-type label.
# Used by detect_body_type to stay in sync with _CONTENT_TYPE_MAP.
# Entries are checked in order; first match wins.
_CT_DETECT_ORDER: Tuple[Tuple[str, str], ...] = (
    ("json",       "raw (JSON)"),
    ("xml",        "raw (XML)"),
    ("urlencoded", "form-urlencoded"),
    ("text",       "raw (text)"),
)


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
    if body_type == "multipart/form-data":
        multipart_data = [r for r in multipart_rows if r.get("key", "").strip()]
        return None, multipart_data

    if body_type == "GraphQL":
        gql_body: Dict[str, Any] = {"query": gql_query}
        if gql_vars:
            try:
                gql_body["variables"] = _json.loads(gql_vars)
            except Exception:
                logger.warning("Failed to parse GraphQL variables as JSON", exc_info=True)
        return _json.dumps(gql_body), None

    return body_text or None, None


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
    if any(k.lower() == "content-type" for k in headers):
        return headers
    ct = _CONTENT_TYPE_MAP.get(body_type)
    if ct is None:
        return headers
    return {**headers, "Content-Type": ct}


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

    if ct:
        for token, label in _CT_DETECT_ORDER:
            if token in ct:
                return label

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
    # Require that the key portion (before the first =) contains no whitespace
    # or path separators, which excludes plain sentences and file paths.
    if "=" in stripped:
        key_part = stripped.split("=", 1)[0]
        if key_part and not any(c in key_part for c in " \t\n\r/\\"):
            return "form-urlencoded"
    return "raw (text)"
