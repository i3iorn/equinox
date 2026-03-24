"""URL handling helpers for Equinox.

This module centralises URL parsing, normalization, and safe placeholder
expansion. It prefers `urlps` when available but falls back to the stdlib
``urllib.parse`` implementation to remain robust in test environments.

High-level policy:
- Interpolation is treated as a pre-processing step (use
  ``expand_placeholders``) before parsing/normalizing a URL.
- Normalization replaces likely-ID segments with placeholders
  (``{id}``, ``{uuid}``, ``{hash}``) to aid matching.
"""

from __future__ import annotations

import json
import re
import logging
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse, parse_qs, urlunparse

try:
    import urlps  # type: ignore
    _HAS_URLPS = True
except Exception:
    _HAS_URLPS = False

from equinox.core.interpolation import VariableInterpolator

logger = logging.getLogger(__name__)


_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_NUMERIC_RE = re.compile(r"^\d+$")
_HEX_RE = re.compile(r"^[0-9a-fA-F]{8,}$")


def _normalize_segment(seg: str) -> str:
    if _UUID_RE.match(seg):
        return "{uuid}"
    if _NUMERIC_RE.match(seg):
        return "{id}"
    if _HEX_RE.match(seg):
        return "{hash}"
    return seg.lower()


def expand_placeholders(url: str, variables: Optional[Dict[str, str]] = None) -> str:
    """Expand {{var}} placeholders in *url* using VariableInterpolator.

    If *variables* is None, the input is returned unchanged.
    """
    if not variables:
        return url
    try:
        return VariableInterpolator.interpolate(url, variables)
    except Exception as exc:
        logger.debug("Placeholder expansion failed for url %r: %s", url, exc)
        # Fall back to raw url if interpolation fails
        return url


def normalized_parts(url: str, variables: Optional[Dict[str, str]] = None) -> Dict[str, object]:
    """Return normalized_url, path_segments, and query_params for *url*.

    Normalization rules are intentionally conservative and reversible: only
    path segments that look like numeric IDs, UUIDs, or long hex strings are
    replaced with placeholders.
    """
    expanded = expand_placeholders(url or "", variables)

    # Prefer urlps when available for robust parsing
    if _HAS_URLPS:
        try:
            parsed = urlps.parse(expanded)  # type: ignore[attr-defined]
            scheme = parsed.scheme or ""
            netloc = parsed.netloc or ""
            path = parsed.path or ""
            query = parsed.query or ""
        except Exception:
            parsed = urlparse(expanded)
            scheme = parsed.scheme
            netloc = parsed.netloc
            path = parsed.path
            query = parsed.query
    else:
        parsed = urlparse(expanded)
        scheme = parsed.scheme
        netloc = parsed.netloc
        path = parsed.path
        query = parsed.query

    # Split and normalise path segments
    raw_segments = [s for s in path.split("/") if s]
    norm_segments: List[str] = [_normalize_segment(s) for s in raw_segments]

    # Build normalized path
    normalized_path = "/" + "/".join(norm_segments) if norm_segments else "/"

    # Parse query params into a deterministic dict (first value only)
    parsed_qs = parse_qs(query, keep_blank_values=True)
    query_params = {k: (v[0] if v else "") for k, v in sorted(parsed_qs.items())}

    # Build a canonical normalized URL (scheme + netloc + normalized_path + sorted query)
    canonical_query = ""
    if query_params:
        pairs = [f"{k}={query_params[k]}" for k in sorted(query_params.keys())]
        canonical_query = "?" + "&".join(pairs)

    netloc_part = netloc.lower()
    normalized_url = f"{scheme}://{netloc_part}{normalized_path}{canonical_query}" if scheme else f"{netloc_part}{normalized_path}{canonical_query}"

    return {
        "normalized_url": normalized_url,
        "path_segments": norm_segments,
        "query_params": query_params,
        "scheme": scheme,
        "netloc": netloc_part,
    }


def normalize_url(url: str, variables: Optional[Dict[str, str]] = None) -> str:
    return normalized_parts(url, variables)["normalized_url"]


def base_path(normalized_url: str) -> str:
    """Return a base path prefix suitable for candidate filtering.

    For example, /users/{id}/posts -> /users
    """
    try:
        parsed = urlparse(normalized_url)
        path = parsed.path or ""
    except Exception:
        # If input is already a path-like normalized_url
        parts = normalized_url.split("?")[0]
        path = parts
    segs = [s for s in path.split("/") if s]
    return "/" + segs[0] if segs else "/"

