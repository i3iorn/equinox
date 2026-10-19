"""URL normalization and placeholder expansion helpers."""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import parse_qsl, quote

from equinox.core.interpolation import VariableInterpolator

from .parsing import _lowercase_netloc_host, _parse_url

logger = logging.getLogger(__name__)

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}" r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$",
)
_NUMERIC_RE = re.compile(r"^\d+$")
_HEX_RE = re.compile(r"^[0-9a-fA-F]{8,}$")


def _normalize_segment(seg: str) -> str:
    if _UUID_RE.match(seg) or _NUMERIC_RE.match(seg):
        return "{id}"
    if _HEX_RE.match(seg):
        return "{hash}"
    return seg.lower()


def _build_canonical_url(
    scheme: str,
    netloc: str,
    path: str,
    query_pairs: list[tuple[str, str]],
) -> str:
    authority = f"{scheme}://{netloc}" if scheme else netloc
    if not query_pairs:
        return f"{authority}{path}"
    query = "&".join(
        f"{quote(key, safe='~')}={quote(value, safe='~')}" for key, value in query_pairs
    )
    return f"{authority}{path}?{query}"


def expand_placeholders(url: str, variables: dict[str, str] | None = None) -> str:
    """Expand {{VAR}} placeholders in URL strings before parsing."""
    if not variables:
        return url
    try:
        return VariableInterpolator.interpolate(url, variables)
    except Exception as exc:
        logger.debug("Placeholder expansion failed for url %r: %s", url, exc)
        return url


def normalized_parts(url: str, variables: dict[str, str] | None = None) -> dict[str, Any]:
    """Decompose URL into normalized components."""
    expanded = expand_placeholders(url or "", variables)
    components = _parse_url(expanded)

    raw_segments = [segment for segment in components.path.split("/") if segment]
    norm_segments: list[str] = [_normalize_segment(segment) for segment in raw_segments]
    normalized_path = "/" + "/".join(norm_segments) if norm_segments else "/"

    # Preserve every key/value pair (duplicates included, input order within a
    # key) so the canonical URL stays faithful to the original request. The
    # exposed ``query_params`` dict keeps the first value per key for
    # consumers that only care about key presence.
    parsed_pairs = parse_qsl(components.query, keep_blank_values=True)
    grouped: dict[str, list[str]] = {}
    query_params: dict[str, str] = {}
    for key, value in parsed_pairs:
        key_str = str(key)
        value_str = str(value)
        grouped.setdefault(key_str, []).append(value_str)
        query_params.setdefault(key_str, value_str)

    query_pairs: list[tuple[str, str]] = []
    for key in sorted(grouped):
        query_pairs.extend((key, value) for value in grouped[key])

    netloc = _lowercase_netloc_host(components.netloc)
    normalized_url = _build_canonical_url(
        components.scheme,
        netloc,
        normalized_path,
        query_pairs,
    )

    return {
        "normalized_url": normalized_url,
        "path_segments": norm_segments,
        "query_params": query_params,
        "scheme": components.scheme,
        "netloc": netloc,
    }


def normalize_url(url: str, variables: dict[str, str] | None = None) -> str:
    """Return canonical normalized URL string."""
    return str(normalized_parts(url, variables)["normalized_url"])


def base_path(normalized_url: str) -> str:
    """Return first path segment from normalized URL."""
    components = _parse_url(normalized_url)
    segments = [segment for segment in components.path.split("/") if segment]
    return f"/{segments[0]}" if segments else "/"
