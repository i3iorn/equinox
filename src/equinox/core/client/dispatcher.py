"""httpx transport layer for the Equinox HTTP client.

Owns the shared ``httpx.Client`` lifecycle, multipart file handling,
SSL context construction, and response wrapping.
"""
from __future__ import annotations

import logging
import ssl
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

from equinox.core.client.auth_redirect import _RedirectSafeAuth
from equinox.core.client.cookie_handler import CookieHandler
from equinox.core.exceptions import ValidationError
from equinox.core.request import Request, Response
from equinox.core.time import utc_now
from equinox.core.validation import Validator

logger = logging.getLogger(__name__)

__all__ = ["HttpxDispatcher"]


class HttpxDispatcher:
    """httpx transport adapter for the Equinox HTTP client pipeline.

    Owns a single long-lived :class:`httpx.Client` that is created lazily on
    the first request (or eagerly via :meth:`open`) and torn down by
    :meth:`close`.  Callers should use :meth:`execute` to send requests —
    all SSL, redirect, multipart, auth, and cookie concerns are handled here.
    """

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

    # ── Client lifecycle ──────────────────────────────────────────────────────

    def _build_ssl_context(self) -> Any:
        """Return an SSL context with TLS 1.2 minimum, or ``False`` to disable."""
        if not self._verify_ssl:
            return False
        ctx = ssl.create_default_context()
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        return ctx

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

    def open(self) -> None:
        """Pre-warm the shared ``httpx.Client`` (called from ``HTTPClient.__enter__``)."""
        self._ensure_client()

    def close(self) -> None:
        """Close and discard the shared ``httpx.Client``."""
        if self._client is not None:
            logger.debug("HttpxDispatcher: closing shared httpx.Client")
            self._client.close()
            self._client = None

    # ── Cookie sync ───────────────────────────────────────────────────────────

    def flush_cookies(self, response: Response, url: str) -> None:
        """Update the in-memory cookie jar from *response* and push to httpx.

        Combines the two-step cookie sync into a single call so no caller
        needs to reach into the dispatcher's private helpers.
        """
        self._cookie_handler.update_from_response(response, url)
        self._sync_cookies_to_client()

    def _sync_cookies_to_client(self) -> None:
        """Merge the latest CookieManager state into the live httpx.Client jar."""
        if self._client is None:
            return
        try:
            for name, value in self._cookie_handler.get_httpx_cookies().items():
                self._client.cookies.set(name, value)
        except Exception as exc:
            logger.debug("HttpxDispatcher: failed to sync cookies to client: %s", exc)

    # ── Multipart ─────────────────────────────────────────────────────────────

    def _build_multipart_files(
        self, request: Request
    ) -> Tuple[Optional[Dict[str, Any]], List[Any]]:
        """Build httpx-compatible multipart files from ``request.files``.

        Accepted shapes per field::

            "field": ("filename", file_bytes_or_fileobj, "content/type")
            "field": Path("/path/to/file")      # opened and closed automatically
            "field": "/path/to/file"            # opened and closed automatically

        Returns:
            A ``(files_dict, opened_handles)`` tuple.  The caller is
            responsible for closing every handle in *opened_handles*.
        """
        if not getattr(request, "files", None):
            return None, []

        files: Dict[str, Any] = {}
        opened_handles: List[Any] = []

        try:
            for field, value in request.files.items():
                if isinstance(value, (str, Path)):
                    fh = Path(value).open("rb")
                    opened_handles.append(fh)
                    files[field] = (Path(value).name, fh)
                elif isinstance(value, tuple) and len(value) in (2, 3):
                    files[field] = value
                else:
                    raise ValidationError(
                        f"Unsupported file spec for field {field!r}: "
                        f"expected a path or (filename, data[, content_type]) tuple"
                    )
        except Exception:
            # Close any handles that were successfully opened before the error.
            for fh in opened_handles:
                try:
                    fh.close()
                except Exception:
                    pass
            raise

        return files, opened_handles

    # ── Response helpers ──────────────────────────────────────────────────────

    def _wrap_response(
        self, raw: httpx.Response, request: Request, elapsed: float
    ) -> Response:
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
        return raw.reason_phrase or httpx.codes.get_reason_phrase(raw.status_code) or ""

    @staticmethod
    def _strip_auto_content_type(
        httpx_req: httpx.Request,
        user_set: bool,
        has_files: bool,
    ) -> None:
        """Remove the ``Content-Type`` httpx injects automatically for body requests.

        Only strips it when the caller did not supply one explicitly *and*
        there are no multipart files (whose boundary httpx must generate).
        """
        if not user_set and not has_files and "content-type" in httpx_req.headers:
            del httpx_req.headers["content-type"]

    @staticmethod
    def _log_redirect_chain(raw: httpx.Response) -> None:
        if not raw.history:
            return
        chain = " → ".join(
            f"{r.status_code} {r.headers.get('location', '?')}" for r in raw.history
        )
        logger.info(
            "Request followed %d redirect(s): %s → %d",
            len(raw.history), chain, raw.status_code,
        )

    # ── Execute ───────────────────────────────────────────────────────────────

    def execute(
        self,
        request: Request,
        headers: Dict[str, str],
        auth_headers: Optional[Dict[str, str]] = None,
    ) -> Response:
        """Send *request* via httpx and return a wrapped :class:`Response`.

        Auth headers are removed from *headers* and passed through httpx's
        native ``auth`` parameter so they survive cross-origin redirects (see
        :class:`~equinox.core.client.auth_redirect._RedirectSafeAuth`).
        """
        client = self._ensure_client()

        logger.debug(
            "HttpxDispatcher: sending %s %s",
            request.method,
            Validator.sanitize_for_display(request.url, 100),
        )

        # Route auth headers through the redirect-safe adapter instead of
        # embedding them directly — httpx strips plain headers on redirects.
        httpx_auth: Optional[_RedirectSafeAuth] = None
        if auth_headers:
            for key in auth_headers:
                headers.pop(key, None)
            httpx_auth = _RedirectSafeAuth(auth_headers)

        multipart_files, opened_handles = self._build_multipart_files(request)
        user_set_content_type = any(k.lower() == "content-type" for k in headers)

        start_time = time.time()
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
                except Exception as exc:
                    logger.debug("Failed to close file handle: %s", exc)

        self._log_redirect_chain(raw)
        elapsed = time.time() - start_time
        logger.debug(
            "HttpxDispatcher: completed in %.3fs — status %d",
            elapsed, raw.status_code,
        )
        return self._wrap_response(raw, request, elapsed)

    # ── Dunder helpers ────────────────────────────────────────────────────────

    def __repr__(self) -> str:
        state = "open" if self._client is not None else "closed"
        return (
            f"HttpxDispatcher(state={state!r}, timeout={self._timeout}, "
            f"verify_ssl={self._verify_ssl}, proxy={self._proxy!r})"
        )
