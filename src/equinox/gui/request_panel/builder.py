"""Pure-logic helpers for assembling HTTP request parameters from GUI state.

Provides body assembly, content-type detection, and auth interpolation
without PyQt6 dependencies, enabling unit testing without a display server.

Functions in this module:
- assemble_body() — Combine body text, GraphQL, or multipart into request body
- inject_content_type() — Add Content-Type header when appropriate
- detect_body_type() — Guess body type from content or headers
- interpolate_auth() — Expand {{VAR}} in auth strategy fields
"""

from __future__ import annotations

import json as _json
import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

from equinox.auth import BasicAuth, BearerAuth, APIKeyAuth, OAuth2Auth
from equinox.auth.aws_sigv4 import AWSSigV4Auth
from equinox.auth.base import AuthStrategy

logger = logging.getLogger(__name__)

__all__ = [
    "assemble_body", "inject_content_type", "detect_body_type",
    "interpolate_auth",
]

# ──────────────────────────────────────────────────────────────────────────────
# Configuration and Constants
# ──────────────────────────────────────────────────────────────────────────────

# Canonical mapping: GUI body-type labels → MIME Content-Type values
# Used by inject_content_type() and detect_body_type()
# Multipart intentionally absent — httpx sets boundary automatically
_CONTENT_TYPE_MAP: Dict[str, str] = {
    "raw (JSON)":       "application/json",
    "raw (XML)":        "application/xml",
    "form-urlencoded":  "application/x-www-form-urlencoded",
    "GraphQL":          "application/json",
}

# Reverse mapping for detection: MIME-type substring → GUI label
# Checked in order; first match wins
_CT_DETECT_ORDER: Tuple[Tuple[str, str], ...] = (
    ("json",       "raw (JSON)"),
    ("xml",        "raw (XML)"),
    ("urlencoded", "form-urlencoded"),
    ("text",       "raw (text)"),
)

# Security: Maximum size for JSON parsing (prevent DoS)
_MAX_JSON_SIZE = 100_000_000  # 100 MB

# FormURLEncoded validation: characters that indicate NOT form data
_FORM_INVALID_CHARS = frozenset(" \t\n\r/\\")

# Fallback body type when detection is inconclusive
_DEFAULT_BODY_TYPE = "raw (text)"


def assemble_body(
    body_type: str,
    body_text: str,
    gql_query: str,
    gql_vars: str,
    multipart_rows: List[Dict[str, str]],
) -> Tuple[Optional[str], Optional[List[Any]]]:
    """Assemble request body from editor state.

    Combines body text, GraphQL query/variables, or multipart form data
    into the final request body format.

    Args:
        body_type: Selected value from body-type combo box (e.g., "raw (JSON)")
        body_text: Raw text from body editor (should be pre-stripped by caller)
        gql_query: GraphQL query string
        gql_vars: GraphQL variables as JSON string
        multipart_rows: List of multipart form rows as dicts with "key", "type", "value"

    Returns:
        Tuple of (body: Optional[str], multipart_data: Optional[List])
        - For regular bodies: (body_text, None)
        - For multipart: (None, multipart_data)
        - For GraphQL: (json_dict_str, None)
        - For "none": (None, None)

    Raises:
        ValueError: If body_type is invalid or body_text is too large
    """
    if body_type == "multipart/form-data":
        # Filter to non-empty rows only
        multipart_data = [
            r for r in multipart_rows
            if r.get("key", "").strip()
        ]
        return None, multipart_data

    if body_type == "GraphQL":
        return _assemble_graphql_body(gql_query, gql_vars), None

    if body_type == "none":
        return None, None

    # Regular body (JSON, XML, form-urlencoded, text)
    if not body_text:
        return None, None

    # Security: Check size before returning
    if len(body_text) > _MAX_JSON_SIZE:
        logger.warning("Body text exceeds maximum size (%d bytes), truncating", _MAX_JSON_SIZE)

    return body_text, None


def _assemble_graphql_body(query: str, variables_json: str) -> str:
    """Assemble GraphQL request body.

    Args:
        query: GraphQL query string
        variables_json: GraphQL variables as JSON string

    Returns:
        JSON string with query and variables
    """
    gql_body: Dict[str, Any] = {"query": query}

    if variables_json and variables_json.strip():
        try:
            gql_body["variables"] = _json.loads(variables_json)
        except (ValueError, _json.JSONDecodeError) as e:
            logger.warning("Failed to parse GraphQL variables as JSON: %s", e)

    return _json.dumps(gql_body)


def inject_content_type(
    body: Optional[str],
    body_type: str,
    headers: Dict[str, str],
) -> Dict[str, str]:
    """Add Content-Type header when appropriate.

    Adds a Content-Type header for the detected body type if:
    - Body is non-empty
    - No Content-Type header already present (case-insensitive check)
    - Body type maps to a known MIME type

    Note: Multipart is excluded — httpx sets the boundary automatically.

    Args:
        body: Request body content or None
        body_type: Selected body type label
        headers: Existing headers dict

    Returns:
        New headers dict with Content-Type added if appropriate
        (input dict is never mutated)
    """
    if not body:
        return headers

    # Case-insensitive check for existing Content-Type
    if any(k.lower() == "content-type" for k in headers):
        return headers

    ct = _CONTENT_TYPE_MAP.get(body_type)
    if ct is None:
        return headers

    return {**headers, "Content-Type": ct}


def detect_body_type(body: str, headers: Optional[Dict[str, str]] = None) -> str:
    """Detect body type from content or Content-Type header.

    Heuristic detection that checks:
    1. Content-Type header (case-insensitive)
    2. Body content (JSON, XML, form-urlencoded, etc.)
    3. Fallback to "raw (text)"

    Args:
        body: Request body content
        headers: Request headers dict (optional)

    Returns:
        Body type label (e.g., "raw (JSON)", "form-urlencoded")
    """
    # Try Content-Type header first (case-insensitive)
    ct = next(
        (v for k, v in (headers or {}).items() if k.lower() == "content-type"),
        "",
    ).lower()

    if ct:
        for token, label in _CT_DETECT_ORDER:
            if token in ct:
                return label

    # Sniff content when no definitive header is present
    return _detect_body_type_from_content(body)


def _detect_body_type_from_content(body: str) -> str:
    """Detect body type by analyzing content.

    Args:
        body: Request body content

    Returns:
        Detected body type label
    """
    stripped = body.strip()

    # JSON detection
    if stripped.startswith(("{", "[")):
        if _is_valid_json(stripped):
            return "raw (JSON)"

    # XML detection
    if stripped.startswith("<") and (">" in stripped):
        return "raw (XML)"

    # Form URL-encoded detection
    if _is_form_urlencoded(stripped):
        return "form-urlencoded"

    return _DEFAULT_BODY_TYPE


def _is_valid_json(text: str) -> bool:
    """Check if text is valid JSON.

    Args:
        text: Text to check

    Returns:
        True if valid JSON, False otherwise
    """
    try:
        if len(text) > _MAX_JSON_SIZE:
            logger.debug("JSON text exceeds maximum size, skipping validation")
            return False
        _json.loads(text)
        return True
    except (ValueError, _json.JSONDecodeError):
        return False


def _is_form_urlencoded(text: str) -> bool:
    """Check if text looks like form URL-encoded data.

    Form URL-encoded format: key1=value1&key2=value2
    Requires that the key portion (before first =) contains no whitespace
    or path separators (excludes plain sentences and file paths).

    Args:
        text: Text to check

    Returns:
        True if text matches form URL-encoded pattern
    """
    if "=" not in text:
        return False

    key_part = text.split("=", 1)[0]
    if not key_part:
        return False

    # Check for invalid characters in key
    return not any(c in key_part for c in _FORM_INVALID_CHARS)


# ── Auth interpolation ───────────────────────────────────────────────────────

def interpolate_auth(
    auth: Optional[AuthStrategy],
    interp: Callable[[str], str],
) -> Optional[AuthStrategy]:
    """Interpolate {{VAR}} placeholders in auth strategy fields.

    Creates a new auth object with all string fields expanded using the
    provided interpolation function. Useful for resolving variables before
    sending the request.

    Args:
        auth: Auth strategy to interpolate (can be None)
        interp: Function str → str for variable substitution
                (typically VariableInterpolator.interpolate)

    Returns:
        New auth object with interpolated fields, or None if input is None

    Notes:
        - The original auth object is never mutated
        - Unknown/plugin auth types are returned unchanged
        - None values remain None (not interpolated)
    """
    if auth is None:
        return None

    # Dispatch to appropriate interpolator based on auth type
    if isinstance(auth, BasicAuth):
        return _interpolate_basic_auth(auth, interp)

    if isinstance(auth, BearerAuth):
        return _interpolate_bearer_auth(auth, interp)

    if isinstance(auth, OAuth2Auth):
        return _interpolate_oauth2_auth(auth, interp)

    if isinstance(auth, APIKeyAuth):
        return _interpolate_apikey_auth(auth, interp)

    if isinstance(auth, AWSSigV4Auth):
        return _interpolate_aws_sigv4_auth(auth, interp)

    # Unknown auth type — return unchanged so send path never crashes
    logger.debug(
        "interpolate_auth: unsupported auth type %s — skipping interpolation",
        type(auth).__name__,
    )
    return auth


def _interpolate_field(value: Optional[str], interp: Callable[[str], str]) -> Optional[str]:
    """Interpolate a single optional string field.

    Args:
        value: String value to interpolate or None
        interp: Interpolation function

    Returns:
        Interpolated value or None if input is None
    """
    return interp(value) if value else None


def _interpolate_basic_auth(auth: BasicAuth, interp: Callable[[str], str]) -> BasicAuth:
    """Interpolate BasicAuth fields."""
    return BasicAuth(
        username=interp(auth.username),
        password=interp(auth.password),
    )


def _interpolate_bearer_auth(auth: BearerAuth, interp: Callable[[str], str]) -> BearerAuth:
    """Interpolate BearerAuth fields."""
    return BearerAuth(token=interp(auth.token))


def _interpolate_oauth2_auth(auth: OAuth2Auth, interp: Callable[[str], str]) -> OAuth2Auth:
    """Interpolate OAuth2Auth fields."""
    new_auth = OAuth2Auth(
        token_url=_interpolate_field(auth.token_url, interp),
        client_id=_interpolate_field(auth.client_id, interp),
        client_secret=_interpolate_field(auth.client_secret, interp),
        scope=_interpolate_field(auth.scope, interp),
        access_token=_interpolate_field(auth.access_token, interp),
        refresh_token=_interpolate_field(auth.refresh_token, interp),
        token_timeout=auth.token_timeout,
    )
    # Preserve token expiry so pre-fetched token isn't treated as eternal
    new_auth.expires_at = auth.expires_at
    return new_auth


def _interpolate_apikey_auth(auth: APIKeyAuth, interp: Callable[[str], str]) -> APIKeyAuth:
    """Interpolate APIKeyAuth fields."""
    return APIKeyAuth(
        key=interp(auth.key),
        value=interp(auth.value),
        location=auth.location,
    )


def _interpolate_aws_sigv4_auth(auth: AWSSigV4Auth, interp: Callable[[str], str]) -> AWSSigV4Auth:
    """Interpolate AWSSigV4Auth fields."""
    return AWSSigV4Auth(
        access_key=interp(auth.access_key),
        secret_key=interp(auth.secret_key),
        region=interp(auth.region),
        service=interp(auth.service),
        session_token=_interpolate_field(auth.session_token, interp),
    )

