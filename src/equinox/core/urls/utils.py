"""URL utility helpers for query/path composition."""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qsl, urlencode


def append_query_params(url: str, params: dict[str, Any], merge_existing: bool = True) -> str:
    """Append or merge query parameters into URL."""
    if not params:
        return url

    safe_params = {str(key): str(value) for key, value in params.items()}
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
