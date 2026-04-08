from typing import Optional

from equinox import Response
from equinox.core.client import logger
from equinox.core.cookies import CookieManager


class CookieHandler:
    def __init__(self, manager: Optional[CookieManager]) -> None:
        self._manager = manager

    def get_httpx_cookies(self) -> dict:
        if self._manager is not None:
            return self._manager.to_httpx_cookies()
        return {}

    def update_from_response(self, response: Optional[Response], url: str) -> None:
        if self._manager is None or response is None:
            logger.debug("CookieHandler: no manager or response, skipping update")
            return
        try:
            headers = dict(response.headers) if response else {}
            if headers.get("set-cookie"):
                logger.debug("CookieHandler: updating cookie jar from Set-Cookie")
                self._manager.update_from_response(headers, url)
            else:
                logger.debug("CookieHandler: no Set-Cookie header present")
        except Exception as exc:
            logger.debug("CookieHandler: update failed: %s", exc)
