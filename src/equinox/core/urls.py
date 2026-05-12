"""URL handling helpers for Equinox.

Public API
----------
- :func:`expand_placeholders` — ``{{VAR}}`` substitution before parsing.
- :func:`normalized_parts` — full URL decomposition with ID-normalised paths.
- :func:`normalize_url` — convenience wrapper returning just the canonical string.
- :func:`base_path` — first path segment for prefix-based candidate filtering.

Design notes
------------
- Variable interpolation is a *pre-processing* step; call
  :func:`expand_placeholders` before parsing when ``{{VAR}}`` tokens may be
  present.
- Normalisation is *conservative and reversible*: only path segments that look
  like numeric IDs, UUIDs, or long hex hashes are replaced with placeholders.
- The optional ``urlps`` library is preferred when available for robust
  edge-case handling; the module falls back to :mod:`urllib.parse`
  transparently.  The parser is selected *once* at module load time via
  :func:`_build_parser` and stored as the :data:`_parse_url` callable so
  there is no per-call branching.
"""

from __future__ import annotations

import re
import logging
from typing import Any, Callable, Dict, List, NamedTuple, Optional, Tuple
from urllib.parse import urlparse, parse_qs, parse_qsl, urlencode

from equinox.core.interpolation import VariableInterpolator

logger = logging.getLogger(__name__)

__all__ = [
    "expand_placeholders",
    "normalized_parts",
    "normalize_url",
    "base_path",
    "url_metadata",
    "parse_query_pairs",
    "append_query_params",
    "join_url_path",
]


# ---------------------------------------------------------------------------
# Compiled patterns for path-segment classification
# ---------------------------------------------------------------------------

_UUID_RE    = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
    r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_NUMERIC_RE = re.compile(r"^\d+$")
_HEX_RE     = re.compile(r"^[0-9a-fA-F]{8,}$")


# ---------------------------------------------------------------------------
# URL components — named tuple for readable field access
# ---------------------------------------------------------------------------

class _URLComponents(NamedTuple):
    """Parsed URL broken into its four fundamental components."""
    scheme: str
    netloc: str
    path:   str
    query:  str


# ---------------------------------------------------------------------------
# Parser selection — encapsulates the optional urlps dependency
# ---------------------------------------------------------------------------

def _build_parser() -> Callable[[str], _URLComponents]:
    """Return the best available URL parser as a single callable.

    Tries ``urlps`` first; falls back to :mod:`urllib.parse` silently when
    ``urlps`` is not installed *or* its ``parse`` API is incompatible.
    The choice is made *once* at module load time so there is zero per-call
    branching overhead.

    Returns:
        A callable ``(url: str) -> _URLComponents``.
    """
    try:
        import urlps  # type: ignore[import]

        # Probe the API with a known-good URL before committing to this parser.
        # urlps may be installed under a different version whose interface differs.
        _probe = urlps.parse("https://example.com")  # type: ignore[attr-defined]
        _ = _probe.scheme, _probe.netloc, _probe.path, _probe.query

        def _urlps_parse(url: str) -> _URLComponents:
            p = urlps.parse(url)  # type: ignore[attr-defined]
            return _URLComponents(
                scheme=p.scheme or "",
                netloc=p.netloc or "",
                path=p.path    or "",
                query=p.query  or "",
            )

        logger.debug("urls: using urlps parser")
        return _urlps_parse
    except Exception:
        pass

    def _stdlib_parse(url: str) -> _URLComponents:
        p = urlparse(url)
        return _URLComponents(p.scheme, p.netloc, p.path, p.query)

    return _stdlib_parse


#: Module-level URL parser — selected once via :func:`_build_parser`.
#: Replace this callable in tests to control parsing behaviour without
#: relying on internal flags (e.g. ``patch("equinox.core.urls._parse_url", …)``).
_parse_url: Callable[[str], _URLComponents] = _build_parser()


# ---------------------------------------------------------------------------
# Path-segment normalisation
# ---------------------------------------------------------------------------

def _normalize_segment(seg: str) -> str:
    """Replace an ID-like path segment with a canonical placeholder.

    Classification rules (applied in order):

    - UUID (``xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx``) or purely numeric
      string → ``"{id}"``
    - Long hex string (≥ 8 contiguous hex characters) → ``"{hash}"``
    - Anything else → lower-cased as-is

    This makes structurally identical paths with different concrete IDs
    compare as equal during deduplication and history matching.

    Args:
        seg: A single URL path segment (no leading or trailing ``/``).

    Returns:
        The original segment lower-cased, or a normalisation placeholder.
    """
    if _UUID_RE.match(seg) or _NUMERIC_RE.match(seg):
        return "{id}"
    if _HEX_RE.match(seg):
        return "{hash}"
    return seg.lower()


# ---------------------------------------------------------------------------
# Canonical URL construction
# ---------------------------------------------------------------------------

def _build_canonical_url(
    scheme: str,
    netloc: str,
    path: str,
    query_params: Dict[str, str],
) -> str:
    """Assemble a canonical URL string from its decomposed components.

    The query string is serialised with keys in lexicographic order so that
    two URLs differing only in parameter order compare as equal.

    Args:
        scheme:       URL scheme (e.g. ``"https"``).
        netloc:       Network location (e.g. ``"api.example.com"``).
        path:         URL path (e.g. ``"/v1/users/{id}"``).
        query_params: Flat ``{key: value}`` query mapping.

    Returns:
        Reassembled URL string.
    """
    authority = f"{scheme}://{netloc}" if scheme else netloc
    if not query_params:
        return f"{authority}{path}"
    query = "&".join(f"{k}={query_params[k]}" for k in sorted(query_params))
    return f"{authority}{path}?{query}"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def expand_placeholders(url: str, variables: Optional[Dict[str, str]] = None) -> str:
    """Expand ``{{VAR}}`` placeholders in *url* using :class:`VariableInterpolator`.

    This should be called *before* any URL parsing so that template tokens
    do not confuse the parser.  When *variables* is ``None`` or empty the
    input is returned unchanged.  Interpolation failures are logged at
    ``DEBUG`` level and the original *url* is returned.

    Args:
        url:       URL string, possibly containing ``{{VAR}}`` tokens.
        variables: Mapping of variable name → value used for substitution.
                   Tokens with no matching key are left as-is.

    Returns:
        URL with all resolvable tokens substituted.
    """
    if not variables:
        return url
    try:
        return VariableInterpolator.interpolate(url, variables)
    except Exception as exc:
        logger.debug("Placeholder expansion failed for url %r: %s", url, exc)
        return url


def normalized_parts(url: str, variables: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Decompose *url* into normalised components.

    Steps applied in order:

    1. Expand ``{{VAR}}`` placeholders via *variables*.
    2. Parse into ``(scheme, netloc, path, query)`` using :data:`_parse_url`.
    3. Normalise each path segment — IDs, UUIDs, and hex hashes become
       ``{id}`` or ``{hash}`` placeholders.
    4. Parse the query string and sort keys for deterministic ordering.
    5. Assemble a canonical URL string via :func:`_build_canonical_url`.

    Args:
        url:       URL to decompose (may contain ``{{VAR}}`` tokens).
        variables: Optional variable map for placeholder expansion.

    Returns:
        Dict with keys:

        - ``"normalized_url"`` — canonical URL string.
        - ``"path_segments"`` — list of normalised path segments.
        - ``"query_params"`` — sorted ``{key: value}`` query mapping.
        - ``"scheme"`` — URL scheme (e.g. ``"https"``).
        - ``"netloc"`` — lower-cased network location.
    """
    expanded   = expand_placeholders(url or "", variables)
    components = _parse_url(expanded)

    raw_segments   = [s for s in components.path.split("/") if s]
    norm_segments: List[str] = [_normalize_segment(s) for s in raw_segments]
    normalized_path = "/" + "/".join(norm_segments) if norm_segments else "/"

    parsed_qs    = parse_qs(components.query, keep_blank_values=True)
    query_params: Dict[str, str] = {
        k: (v[0] if v else "")
        for k, v in sorted(parsed_qs.items())
    }

    netloc_lower   = components.netloc.lower()
    normalized_url = _build_canonical_url(
        components.scheme, netloc_lower, normalized_path, query_params
    )

    return {
        "normalized_url": normalized_url,
        "path_segments":  norm_segments,
        "query_params":   query_params,
        "scheme":         components.scheme,
        "netloc":         netloc_lower,
    }


def normalize_url(url: str, variables: Optional[Dict[str, str]] = None) -> str:
    """Return the canonical normalised URL string for *url*.

    Thin convenience wrapper around :func:`normalized_parts` for callers
    that only need the canonical string rather than the full decomposition.

    Args:
        url:       URL to normalise.
        variables: Optional variable map for placeholder expansion.

    Returns:
        Canonical normalised URL string.
    """
    return normalized_parts(url, variables)["normalized_url"]


def base_path(normalized_url: str) -> str:
    """Return the first path segment of *normalized_url*.

    Used as a quick prefix filter to narrow candidate URLs before running
    more expensive normalisation.  Examples::

        base_path("https://api.example.com/users/{id}/posts")  # "/users"
        base_path("https://example.com/")                       # "/"
        base_path("")                                            # "/"

    Args:
        normalized_url: A URL string (typically from :func:`normalize_url`).

    Returns:
        ``"/<first-segment>"`` or ``"/"`` when the path has no segments.
    """
    components = _parse_url(normalized_url)
    segs = [s for s in components.path.split("/") if s]
    return f"/{segs[0]}" if segs else "/"


def _split_host_port(netloc: str) -> Tuple[str, Optional[int]]:
    """Best-effort netloc split into host and optional port."""
    right = (netloc or "").rsplit("@", 1)[-1].strip()
    if not right:
        return "", None

    if right.startswith("["):
        end = right.find("]")
        if end != -1:
            host = right[1:end]
            tail = right[end + 1 :]
            if tail.startswith(":") and tail[1:].isdigit():
                try:
                    return host.lower(), int(tail[1:])
                except Exception:
                    return host.lower(), None
            return host.lower(), None

    if right.count(":") == 1:
        host, port_text = right.split(":", 1)
        if port_text.isdigit():
            try:
                return host.lower(), int(port_text)
            except Exception:
                return host.lower(), None

    return right.lower(), None


def url_metadata(url: str) -> Dict[str, Any]:
    """Return parsed URL metadata for callers that need raw URL components."""
    parsed = _parse_url(url or "")
    fragment = ""
    if "#" in (url or ""):
        _, _, fragment = (url or "").partition("#")
    host, port = _split_host_port(parsed.netloc)
    return {
        "scheme": parsed.scheme or "",
        "netloc": parsed.netloc or "",
        "path": parsed.path or "",
        "query": parsed.query or "",
        "fragment": fragment,
        "hostname": host,
        "port": port,
    }


def parse_query_pairs(query: str, keep_blank_values: bool = True) -> List[Tuple[str, str]]:
    """Parse a URL query string to ordered key/value tuples."""
    return parse_qsl(query or "", keep_blank_values=keep_blank_values)


def append_query_params(url: str, params: Dict[str, Any], merge_existing: bool = True) -> str:
    """Append or merge query parameters into *url*.

    When *merge_existing* is True, existing query keys are overridden by *params*.
    """
    if not params:
        return url

    safe_params = {str(k): str(v) for k, v in params.items()}
    before_frag, has_frag, fragment = (url or "").partition("#")
    base, has_q, existing_query = before_frag.partition("?")

    if merge_existing:
        merged = dict(parse_qsl(existing_query, keep_blank_values=True))
        merged.update(safe_params)
        query = urlencode(merged, doseq=False)
        rebuilt = f"{base}?{query}" if query else base
    else:
        extra = urlencode(safe_params, doseq=False)
        if has_q and existing_query:
            rebuilt = f"{base}?{existing_query}&{extra}"
        elif has_q:
            rebuilt = f"{base}?{extra}"
        else:
            rebuilt = f"{base}?{extra}"

    return f"{rebuilt}#{fragment}" if has_frag else rebuilt


def join_url_path(base_url: str, path: str) -> str:
    """Join a base URL and relative path with predictable slash handling."""
    base = (base_url or "").rstrip("/")
    rel = (path or "").lstrip("/")
    if not base:
        return "/" + rel if rel else "/"
    if not rel:
        return base
    return f"{base}/{rel}"

