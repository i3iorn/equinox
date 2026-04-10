"""Redirect-safe httpx auth adapter.

Internal module — consumed by :mod:`equinox.core.client.dispatcher`.
"""
from __future__ import annotations

import logging
from typing import Dict, Generator

import httpx

logger = logging.getLogger(__name__)


class _RedirectSafeAuth(httpx.Auth):
    """httpx ``Auth`` adapter that re-injects auth headers on every request leg.

    httpx strips ``Authorization`` (and ``Cookie``) headers when following
    cross-origin redirects (different scheme, host, or port).  This is
    correct per RFC 7235 §2.2 for untrusted redirects, but breaks many
    real-world OAuth2/Bearer flows where the same auth is required after a
    scheme upgrade (HTTP → HTTPS) or a load-balancer redirect.

    Passing auth through httpx's native ``auth`` parameter causes
    ``auth_flow`` to be re-executed on *every* leg of the redirect chain,
    ensuring the injected headers are always present regardless of origin
    changes.
    """

    def __init__(self, auth_headers: Dict[str, str]) -> None:
        if not auth_headers:
            raise ValueError("auth_headers must not be empty")
        self._auth_headers = dict(auth_headers)  # defensive copy

    def auth_flow(
        self, request: httpx.Request
    ) -> Generator[httpx.Request, httpx.Response, None]:
        """Inject stored auth headers then yield the request for sending."""
        for key, value in self._auth_headers.items():
            request.headers[key] = value
        logger.debug(
            "_RedirectSafeAuth: injected %d auth header(s) for %s %s",
            len(self._auth_headers),
            request.method,
            request.url,
        )
        yield request

    def __repr__(self) -> str:
        keys = sorted(self._auth_headers.keys())
        return f"_RedirectSafeAuth(headers={keys})"
