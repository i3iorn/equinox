"""Cookie jar bridge between CookieManager and the httpx transport."""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from equinox.core.cookies import CookieManager
from equinox.core.request import Response

logger = logging.getLogger(__name__)

__all__ = ["CookieHandler"]


class CookieHandler:
    """Bridge between a :class:`~equinox.core.cookies.CookieManager` and the
    HTTP client pipeline.

    Wraps the optional cookie manager so the rest of the pipeline can call
    cookie operations unconditionally — all methods are no-ops when no
    manager is configured.
    """

    def __init__(self, manager: Optional[CookieManager]) -> None:
        self._manager = manager

    # ── Public API ────────────────────────────────────────────────────────────

    def get_httpx_cookies(self) -> Dict[str, Any]:
        """Return a cookie dict suitable for passing to httpx, or ``{}``."""
        if self._manager is not None:
            return self._manager.to_httpx_cookies()
        return {}

    def update_from_response(self, response: Optional[Response], url: str) -> None:
        """Persist any ``Set-Cookie`` headers from *response* into the jar.

        No-ops silently when no manager is configured or *response* is ``None``.
        Cookie-update errors are caught and logged at DEBUG level because
        cookie handling is always best-effort.
        """
        if self._manager is None or response is None:
            return

        try:
            headers = dict(response.headers)
            if any(k.lower() == "set-cookie" for k in headers):
                logger.debug(
                    "CookieHandler: updating cookie jar from Set-Cookie (url=%s)", url
                )
                self._manager.update_from_response(headers, url)
        except Exception as exc:
            logger.debug("CookieHandler: cookie update failed for %s: %s", url, exc)

    # ── Dunder helpers ────────────────────────────────────────────────────────

    def __repr__(self) -> str:
        managed = self._manager is not None
        return f"CookieHandler(managed={managed})"
