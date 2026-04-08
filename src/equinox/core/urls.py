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

import re
import logging
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse, parse_qs

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
    """Replace ID-like path segments with a generic placeholder.

    UUID and numeric segments become ``{id}``; long hex strings become
    ``{hash}``.  All other segments are lowercased.  This lets structurally
    identical paths with different IDs compare as equal.
    """
    if _UUID_RE.match(seg) or _NUMERIC_RE.match(seg):
        return "{id}"
    if _HEX_RE.match(seg):
        return "{hash}"
    return seg.lower()


def _parse_url(url: str) -> Tuple[str, str, str, str]:
    """Return ``(scheme, netloc, path, query)`` for *url*.

    Uses ``urlps`` when available for robust parsing; falls back to
    ``urllib.parse`` transparently.
    """
    if _HAS_URLPS:
        try:
            p = urlps.parse(url)  # type: ignore[attr-defined]
            return p.scheme or "", p.netloc or "", p.path or "", p.query or ""
        except Exception:
            pass
    p = urlparse(url)
    return p.scheme, p.netloc, p.path, p.query


def _build_canonical_url(
    scheme: str, netloc: str, path: str, query_params: Dict[str, str]
) -> str:
    """Build a canonical URL with a deterministically-sorted query string."""
    query = ""
    if query_params:
        query = "?" + "&".join(f"{k}={query_params[k]}" for k in sorted(query_params))
    authority = f"{scheme}://{netloc}" if scheme else netloc
    return f"{authority}{path}{query}"


def expand_placeholders(url: str, variables: Optional[Dict[str, str]] = None) -> str:
    """Expand {{var}} placeholders in *url* using VariableInterpolator.

    If *variables* is None or empty, the input is returned unchanged.
    """
    if not variables:
        return url
    try:
        return VariableInterpolator.interpolate(url, variables)
    except Exception as exc:
        logger.debug("Placeholder expansion failed for url %r: %s", url, exc)
        return url


def normalized_parts(url: str, variables: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Return normalized_url, path_segments, and query_params for *url*.

    Normalization rules are intentionally conservative and reversible: only
    path segments that look like numeric IDs, UUIDs, or long hex strings are
    replaced with placeholders.
    """
    expanded = expand_placeholders(url or "", variables)
    scheme, netloc, path, query = _parse_url(expanded)

    raw_segments = [s for s in path.split("/") if s]
    norm_segments: List[str] = [_normalize_segment(s) for s in raw_segments]
    normalized_path = "/" + "/".join(norm_segments) if norm_segments else "/"

    parsed_qs = parse_qs(query, keep_blank_values=True)
    query_params = {k: (v[0] if v else "") for k, v in sorted(parsed_qs.items())}

    netloc_lower = netloc.lower()
    normalized_url = _build_canonical_url(scheme, netloc_lower, normalized_path, query_params)

    return {
        "normalized_url": normalized_url,
        "path_segments": norm_segments,
        "query_params": query_params,
        "scheme": scheme,
        "netloc": netloc_lower,
    }


def normalize_url(url: str, variables: Optional[Dict[str, str]] = None) -> str:
    """Return the canonical normalized URL string for *url*.

    Convenience wrapper around :func:`normalized_parts` when only the
    normalized URL string is needed.
    """
    return normalized_parts(url, variables)["normalized_url"]


def base_path(normalized_url: str) -> str:
    """Return the first path segment — used as a prefix for candidate filtering.

    For example: ``/users/{id}/posts`` → ``/users``.
    """
    path = urlparse(normalized_url).path
    segs = [s for s in path.split("/") if s]
    return "/" + segs[0] if segs else "/"

