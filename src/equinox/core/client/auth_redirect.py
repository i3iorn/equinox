from typing import Dict

import httpx


class _RedirectSafeAuth(httpx.Auth):
    """httpx Auth adapter that re-applies auth headers after redirects.

    httpx strips ``Authorization`` (and ``Cookie``) headers when following
    cross-origin redirects (different scheme, host, or port).  This is
    correct per RFC 7235 §2.2 for untrusted redirects, but breaks many
    real-world OAuth2/Bearer flows where the same auth is required
    after a scheme upgrade (HTTP → HTTPS) or a load-balancer redirect.

    By passing auth through httpx's native ``auth`` parameter instead of
    as a plain header, the auth flow is re-executed on every leg of the
    redirect chain, ensuring the ``Authorization`` header is present.
    """

    def __init__(self, auth_headers: Dict[str, str]) -> None:
        self._auth_headers = auth_headers

    def auth_flow(self, request: httpx.Request):
        for key, value in self._auth_headers.items():
            request.headers[key] = value
        yield request
