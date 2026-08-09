"""URL parser selection and metadata helpers."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, NamedTuple
from urllib.parse import parse_qsl, urlparse

logger = logging.getLogger(__name__)


class URLComponents(NamedTuple):
    """Parsed URL broken into its four fundamental components."""

    scheme: str
    netloc: str
    path: str
    query: str


def _stdlib_parse(url: str) -> URLComponents:
    parsed = urlparse(url)
    return URLComponents(parsed.scheme, parsed.netloc, parsed.path, parsed.query)


def _build_parser() -> Callable[[str], URLComponents]:
    """Return the best available URL parser as a single callable."""
    try:
        import urlps
        from urlps.exceptions import URLpError

        _probe = urlps.parse_url_unsafe("https://example.com")
        _ = _probe.scheme, _probe.netloc, _probe.path, _probe.query

        def _urlps_parse(url: str) -> URLComponents:
            # parse_url_unsafe() raises on malformed/incomplete input (e.g. "",
            # "http://"), unlike urlparse(); callers of this module rely on a
            # parser that always returns best-effort components, so fall back
            # to the stdlib splitter for whatever urlps refuses to parse.
            try:
                parsed = urlps.parse_url_unsafe(url)
            except URLpError:
                return _stdlib_parse(url)
            return URLComponents(
                scheme=parsed.scheme or "",
                netloc=parsed.netloc or "",
                path=parsed.path or "",
                query=parsed.query or "",
            )

        logger.debug("urls: using urlps parser")
        return _urlps_parse
    except Exception:
        pass

    return _stdlib_parse


_parse_url: Callable[[str], URLComponents] = _build_parser()


def _split_host_port(netloc: str) -> tuple[str, int | None]:
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


def url_metadata(url: str) -> dict[str, Any]:
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


def parse_query_pairs(query: str, keep_blank_values: bool = True) -> list[tuple[str, str]]:
    """Parse a URL query string to ordered key/value tuples."""
    return parse_qsl(query or "", keep_blank_values=keep_blank_values)
