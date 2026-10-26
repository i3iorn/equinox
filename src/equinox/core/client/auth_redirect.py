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
    """httpx ``Auth`` adapter carrying auth headers on the initial request leg.

    httpx's native ``auth`` parameter injects the adapter's headers on the
    initial request leg, then handles redirect legs itself.  Verified against
    httpx's ``_redirect_headers`` (``_client.py``) — on redirect hops:

    * ``Authorization`` is **stripped** for non-same-origin hops, *except*
      direct HTTP→HTTPS scheme upgrades, which keep it (the OAuth2
      load-balancer/scheme-upgrade use case below).
    * ``Cookie`` is **always dropped** — the client cookie store governs
      cookies on hops.

    ``auth_flow`` is executed **once per ``send``**: it is re-invoked via
    ``auth_flow.send(response)`` only for multi-yield (single-sign-on) flows,
    which this adapter is not.  The earlier claim that it was re-executed on
    every redirect leg was factually wrong and is what motivated this module;
    a regression test (``tests/core/client/test_auth_redirect.py``) pins the
    actual security property so the false claim cannot silently return.

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

        Executed once per ``send`` on the initial request leg.  Redirect hops
        are governed by httpx's own ``_redirect_headers`` policy: cross-origin
        hops strip ``Authorization`` (scheme upgrades excepted) and always
        drop ``Cookie`` — see the class docstring.

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
