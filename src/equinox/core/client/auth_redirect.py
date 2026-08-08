"""Redirect-safe httpx auth adapter.

Internal module — consumed by :mod:`equinox.core.client.dispatcher`.
"""

from __future__ import annotations

import logging
from collections.abc import Generator

import httpx
from equinox.security import redact_url

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

    Args:
        auth_headers: Dict of HTTP headers to inject (must not be empty).

    Raises:
        ValueError: If auth_headers is empty.
    """

    def __init__(self, auth_headers: dict[str, str]) -> None:
        if not auth_headers:
            raise ValueError(
                "auth_headers required for redirect-safe auth (must contain at least one header)",
            )
        # Defensive copy prevents external modification of stored headers
        self._auth_headers = dict(auth_headers)

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response, None]:
        """Inject stored auth headers then yield the request for sending.

        Re-executed on every redirect leg, ensuring auth headers persist
        across origin changes (scheme, host, port).

        Args:
            request: The HTTP request being sent.

        Yields:
            The modified request with auth headers injected.
        """
        for key, value in self._auth_headers.items():
            request.headers[key] = value
        logger.debug(
            "_RedirectSafeAuth: injected %d auth header(s) for %s %s",
            len(self._auth_headers),
            request.method,
            redact_url(str(request.url)),
        )
        yield request

    def __repr__(self) -> str:
        """Return developer-friendly representation."""
        keys = sorted(self._auth_headers.keys())
        return f"_RedirectSafeAuth(headers={keys})"
