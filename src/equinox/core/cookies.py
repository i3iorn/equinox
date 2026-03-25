"""Cookie manager interface for HTTPClient.

This small module defines the expected contract for cookie managers used by
`HTTPClient`. It is intentionally minimal: implementations may be application
specific (DB-backed, in-memory, or third-party), but should expose the two
methods used by the client:

- `to_httpx_cookies() -> dict`: return an httpx-compatible cookie dict
  (mapping name -> value) to seed the client.
- `update_from_response(response_headers: dict, url: str) -> None`: update the
  cookie store from response headers (typically by parsing Set-Cookie).

Having an explicit Protocol/ABC improves discoverability and enables
static typing for `HTTPClient` consumers.
"""
from typing import Protocol, Dict, Any


class CookieManager(Protocol):
    """Protocol describing the minimal cookie manager interface expected by
    `HTTPClient`.
    """

    def to_httpx_cookies(self) -> Dict[str, Any]:
        """Return cookies in an httpx-compatible mapping (name -> value)."""

    def update_from_response(self, response_headers: Dict[str, str], url: str) -> None:
        """Update the cookie store from response headers and the request URL.

        The implementation should safely ignore missing Set-Cookie headers.
        """


class InMemoryCookieManager:
    """A tiny in-memory cookie manager useful for tests and non-persistent use.

    Implements the minimal API expected by :class:`CookieManager`.
    """

    def __init__(self) -> None:
        self._cookies: Dict[str, str] = {}

    def to_httpx_cookies(self) -> Dict[str, str]:
        return dict(self._cookies)

    def update_from_response(self, response_headers: Dict[str, str], url: str) -> None:
        # Very small parser: respect Set-Cookie headers with single name=value entries.
        for k, v in response_headers.items():
            if k.lower() == "set-cookie":
                try:
                    pair = v.split(";", 1)[0].strip()
                    if "=" in pair:
                        name, value = pair.split("=", 1)
                        self._cookies[name.strip()] = value.strip()
                except Exception:
                    # Best-effort: ignore malformed Set-Cookie values
                    continue

