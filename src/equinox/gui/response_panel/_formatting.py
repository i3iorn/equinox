"""Response formatting utilities.

Extracted into a separate module to improve encapsulation,
testability, and separation of concerns. Handles:

- Human-readable size formatting
- Pretty-printing JSON and XML responses
- Parsing Set-Cookie headers with full attribute extraction

Security: Uses defusedxml when available to prevent XXE attacks.
"""

from __future__ import annotations

import http.cookies as _hc
import json
import logging
from collections.abc import Iterable
from typing import Any

from equinox.core.request import Response

logger = logging.getLogger(__name__)

# Security: Use defusedxml to prevent XXE attacks (XML external entity injection)
# If defusedxml is not available, fall back to standard library with warnings
try:
    import defusedxml.minidom as _SAFE_MINIDOM  # type: ignore

    _HAS_DEFUSEDXML = True
except ImportError:
    import xml.dom.minidom as _SAFE_MINIDOM  # type: ignore

    _HAS_DEFUSEDXML = False
    logger.warning(
        "defusedxml not available; XML parsing will use standard library. "
        "For production use, install defusedxml: pip install defusedxml"
    )


def format_size(size: int) -> str:
    """Convert bytes to human-readable size string.

    Args:
        size: Size in bytes

    Returns:
        Human-readable size (e.g., "1.2 MB")
    """
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{size} {unit}"
        size /= 1024.0
    return f"{size:.1f} GB"


def pretty_print_body(response: Response) -> str:
    """Format response body for display (JSON or XML, otherwise raw).

    Attempts to pretty-print based on content-type detection.
    Falls back to raw text if format detection fails.

    Args:
        response: Response object with headers and body

    Returns:
        Formatted body string
    """
    # Try JSON first if detected
    if getattr(response, "is_json", False):
        try:
            return json.dumps(response.json(), indent=2, ensure_ascii=False)
        except Exception as e:
            logger.debug("JSON pretty-print failed: %s", e)

    # Try XML/HTML if detected by content-type
    content_type = response.headers.get("content-type", "").lower()
    if any(token in content_type for token in ("xml", "html", "svg")):
        return _pretty_print_xml(response.text)

    # Return raw text as fallback
    return response.text


def _pretty_print_xml(text: str) -> str:
    """Pretty-print XML/HTML content.

    Uses defusedxml when available for security. Falls back to
    raw text if parsing fails.

    Args:
        text: XML/HTML text to format

    Returns:
        Pretty-printed XML or original text if parsing fails
    """
    try:
        parsed = _SAFE_MINIDOM.parseString(text.encode("utf-8"))
        return parsed.toprettyxml(indent="  ")
    except Exception as e:
        logger.debug("XML pretty-print failed: %s", e)
        return text


def parse_cookies(headers: Any) -> list[tuple[str, dict[str, str]]]:
    """Parse Set-Cookie headers into structured cookie data.

    Extracts all Set-Cookie headers and parses them into a list of
    tuples containing (cookie_name, attributes_dict).

    Args:
        headers: Header container, dict-like object, or iterable of (name, value)

    Returns:
        List of (cookie_name, attributes) tuples where attributes
        is a dict with keys: value, domain, path, expires, secure, httponly
    """
    cookies: list[tuple[str, dict[str, str]]] = []

    for value in _iter_set_cookie_values(headers):
        try:
            cookies.extend(_parse_cookie_header(value))
        except Exception as e:
            logger.debug("Cookie parsing failed for header value: %s", e)
            # Add raw cookie for display
            cookies.append(("(raw)", {"value": value}))

    return cookies


def _iter_set_cookie_values(headers: Any) -> Iterable[str]:
    """Yield all Set-Cookie header values from different header container shapes."""
    if headers is None:
        return []

    # Common multi-value APIs first.
    get_list = getattr(headers, "get_list", None)
    if callable(get_list):
        try:
            values = get_list("set-cookie") or []
            return [str(v) for v in values if v is not None]
        except Exception:
            logger.debug("Header get_list('set-cookie') failed", exc_info=True)

    get_all = getattr(headers, "get_all", None)
    if callable(get_all):
        try:
            values = get_all("set-cookie") or []
            return [str(v) for v in values if v is not None]
        except Exception:
            logger.debug("Header get_all('set-cookie') failed", exc_info=True)

    # Fallback: dict-like or iterable pair handling.
    try:
        items = headers.items() if hasattr(headers, "items") else headers
        out: list[str] = []
        for key, value in items:
            if str(key).lower() == "set-cookie" and value is not None:
                out.append(str(value))
        return out
    except Exception:
        logger.debug("Failed iterating cookie headers", exc_info=True)
        return []


def _parse_cookie_header(value: str) -> list[tuple[str, dict[str, str]]]:
    """Parse a single Set-Cookie header value.

    Args:
        value: Raw Set-Cookie header value

    Returns:
        List of (cookie_name, attributes) tuples where attributes
        dict contains: value, domain, path, expires, secure, httponly

    Raises:
        Exception: If parsing fails
    """
    cookies: list[tuple[str, dict[str, str]]] = []
    morsel_obj = _hc.SimpleCookie()
    morsel_obj.load(value)

    for cookie_name, morsel in morsel_obj.items():
        attributes = {
            "value": morsel.value or "",
            "domain": morsel.get("domain") or "",
            "path": morsel.get("path") or "",
            "expires": morsel.get("expires") or "",
            "secure": "true" if morsel.get("secure") else "false",
            "httponly": "true" if morsel.get("httponly") else "false",
        }
        cookies.append((cookie_name, attributes))

    return cookies
