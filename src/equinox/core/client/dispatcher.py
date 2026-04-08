import os
import ssl
import time
from pathlib import Path
from typing import Optional, Any, Tuple, Dict, List

import httpx

from equinox import Request, Response
from equinox.core.client import CookieHandler, logger, _RedirectSafeAuth
from equinox.core.exceptions import ValidationError
from equinox.core.time import utc_now
from equinox.core.validation import Validator


class HttpxDispatcher:
    def __init__(
        self,
        timeout: float,
        follow_redirects: bool,
        verify_ssl: bool,
        proxy: Optional[str],
        cookie_handler: CookieHandler,
    ) -> None:
        self._timeout = timeout
        self._follow_redirects = follow_redirects
        self._verify_ssl = verify_ssl
        self._proxy = proxy
        self._cookie_handler = cookie_handler
        self._client: Optional[httpx.Client] = None

    # SSL context builder
    def _build_ssl_context(self) -> Any:
        if not self._verify_ssl:
            return False
        ssl_context = ssl.create_default_context()
        ssl_context.minimum_version = ssl.TLSVersion.TLSv1_2
        return ssl_context

    def _ensure_client(self) -> httpx.Client:
        if self._client is None:
            logger.debug("HttpxDispatcher: creating shared httpx.Client")
            self._client = httpx.Client(
                timeout=self._timeout,
                follow_redirects=self._follow_redirects,
                verify=self._build_ssl_context(),
                proxy=self._proxy,
                cookies=self._cookie_handler.get_httpx_cookies(),
            )
        return self._client

    def _sync_cookies_to_client(self) -> None:
        """Merge the latest CookieManager state into the live httpx.Client jar.

        Called after every response so that cookies received via ``Set-Cookie``
        are available for the next request without rebuilding the whole client.
        """
        if self._client is None:
            return
        try:
            fresh = self._cookie_handler.get_httpx_cookies()
            for name, value in fresh.items():
                self._client.cookies.set(name, value)
        except Exception as exc:
            logger.debug("HttpxDispatcher: failed to sync cookies to client: %s", exc)

    def close(self) -> None:
        if self._client is not None:
            logger.debug("HttpxDispatcher: closing shared httpx.Client")
            self._client.close()
            self._client = None

    # Multipart builder (simple, DRY)
    def _build_multipart_files(
        self, request: Request
    ) -> Tuple[Optional[Dict[str, Any]], List[Any]]:
        """Build httpx-compatible multipart files from request.files (if any).

        Expected shape:
            request.files = {
                "field": ("filename", file_bytes_or_fileobj, "content/type")
            }
        """
        if not getattr(request, "files", None):
            return None, []

        files: Dict[str, Any] = {}
        opened_handles: List[Any] = []

        for field, value in request.files.items():
            # value can be:
            # - (filename, fileobj/bytes, content_type)
            # - Path / str (path)
            if isinstance(value, (str, Path)):
                fh = open(value, "rb")
                opened_handles.append(fh)
                files[field] = (os.path.basename(str(value)), fh)
            elif isinstance(value, tuple) and len(value) in (2, 3):
                files[field] = value
            else:
                raise ValidationError(f"Unsupported file spec for field '{field}'")

        return files, opened_handles

    def _wrap_response(self, raw: httpx.Response, request: Request, elapsed: float) -> Response:
        return Response(
            status_code=raw.status_code,
            reason=self._extract_reason_phrase(raw),
            headers=dict(raw.headers),
            body=raw.content,
            elapsed=elapsed,
            request=request,
            timestamp=utc_now(),
        )

    @staticmethod
    def _extract_reason_phrase(raw: httpx.Response) -> str:
        # httpx doesn't expose reason phrase directly; approximate from status code
        return raw.reason_phrase or httpx.codes.get_reason_phrase(raw.status_code) or ""

    @staticmethod
    def _strip_auto_content_type(
        httpx_req: httpx.Request,
        user_set: bool,
        has_files: bool,
    ) -> None:
        """Remove the ``content-type`` header that httpx injects automatically.

        Only strips it when the caller did not set one explicitly *and* there
        are no multipart files (whose boundary httpx must generate itself).
        """
        if not user_set and not has_files and "content-type" in httpx_req.headers:
            del httpx_req.headers["content-type"]

    @staticmethod
    def _log_redirect_chain(raw: httpx.Response) -> None:
        """Log the redirect chain when the request was redirected one or more times."""
        if not raw.history:
            return
        chain = " → ".join(
            f"{r.status_code} {r.headers.get('location', '?')}" for r in raw.history
        )
        logger.info(
            "Request followed %d redirect(s): %s → %d",
            len(raw.history),
            chain,
            raw.status_code,
        )

    def execute(
        self,
        request: Request,
        headers: Dict[str, str],
        auth_headers: Optional[Dict[str, str]] = None,
    ) -> Response:
        """Execute the request using httpx, handling redirects and multipart."""
        client = self._ensure_client()

        logger.debug(
            "HttpxDispatcher: sending %s request to %s",
            request.method,
            Validator.sanitize_for_display(request.url, 100),
        )
        start_time = time.time()

        httpx_auth: Optional[_RedirectSafeAuth] = None
        if auth_headers:
            for key in auth_headers:
                headers.pop(key, None)
            httpx_auth = _RedirectSafeAuth(auth_headers)

        multipart_files, opened_handles = self._build_multipart_files(request)
        user_set_content_type = any(k.lower() == "content-type" for k in headers)

        try:
            httpx_request = client.build_request(
                method=request.method,
                url=request.url,
                headers=headers,
                params=request.params,
                content=(
                    request.body.encode("utf-8")
                    if (request.body and not multipart_files)
                    else None
                ),
                files=multipart_files or None,
                timeout=request.timeout or self._timeout,
            )

            self._strip_auto_content_type(
                httpx_request, user_set_content_type, bool(multipart_files)
            )

            raw = client.send(
                httpx_request,
                follow_redirects=(
                    request.follow_redirects
                    if request.follow_redirects is not None
                    else self._follow_redirects
                ),
                auth=httpx_auth,
            )
        finally:
            for fh in opened_handles:
                try:
                    fh.close()
                except Exception as close_exc:
                    logger.debug("Failed to close file handle: %s", close_exc)

        self._log_redirect_chain(raw)

        elapsed = time.time() - start_time
        logger.debug(
            "HttpxDispatcher: request completed in %.2fs with status %d",
            elapsed,
            raw.status_code,
        )
        return self._wrap_response(raw, request, elapsed)
