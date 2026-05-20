"""httpx transport layer for the Equinox HTTP client.

Owns the shared ``httpx.Client`` lifecycle, multipart file handling,
SSL context construction, and response wrapping.
"""

from __future__ import annotations

import logging
import ssl
import threading
import time
from pathlib import Path
from typing import Any

import httpx

from equinox.core.client.auth_redirect import _RedirectSafeAuth
from equinox.core.client.cookie_handler import CookieHandler
from equinox.core.exceptions import ValidationError
from equinox.core.request import Request, Response
from equinox.core.util.time import utc_now
from equinox.core.validation import Validator
from equinox.security import redact_headers, redact_url

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
        proxy: str | None,
        cookie_handler: CookieHandler,
    ) -> None:
        self._timeout = timeout
        self._follow_redirects = follow_redirects
        self._verify_ssl = verify_ssl
        self._proxy = proxy
        self._cookie_handler = cookie_handler
        self._clients: dict[bool, httpx.Client] = {}
        self._client_lock = threading.Lock()

    # ── Client lifecycle ──────────────────────────────────────────────────────

    def _build_ssl_context(self) -> Any:
        """Return an SSL context with TLS 1.2 minimum, or ``False`` to disable."""
        if not self._verify_ssl:
            return False
        ctx = ssl.create_default_context()
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        return ctx

    def _ensure_client(self, verify_ssl: bool = True) -> httpx.Client:
        client_key = bool(verify_ssl)
        with self._client_lock:
            existing = self._clients.get(client_key)
            if existing is not None:
                return existing
            logger.debug(
                "HttpxDispatcher: creating shared httpx.Client (verify_ssl=%s)",
                client_key,
            )
            created = httpx.Client(
                timeout=self._timeout,
                follow_redirects=self._follow_redirects,
                verify=self._build_ssl_context() if client_key else False,
                proxy=self._proxy,
            )
            self._apply_cookie_records_to_client(created)
            self._clients[client_key] = created
            return created

    def open(self) -> None:
        """Pre-warm the shared ``httpx.Client`` (called from ``HTTPClient.__enter__``)."""
        self._ensure_client()

    def close(self) -> None:
        """Close and discard the shared ``httpx.Client``."""
        with self._client_lock:
            clients = list(self._clients.values())
            self._clients.clear()
        for client in clients:
            try:
                client.close()
            except Exception as exc:
                logger.debug("HttpxDispatcher: failed to close client cleanly: %s", exc)

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
        with self._client_lock:
            clients = list(self._clients.values())
        if not clients:
            return
        try:
            cookie_records = self._cookie_handler.get_httpx_cookie_records()
            for client in clients:
                self._apply_cookie_records_to_client(client, cookie_records)
        except Exception as exc:
            logger.debug("HttpxDispatcher: failed to sync cookies to client: %s", exc)

    def _apply_cookie_records_to_client(
        self,
        client: httpx.Client,
        cookie_records: list[dict[str, str]] | None = None,
    ) -> None:
        """Replace client cookie jar from manager records (name/value/domain/path)."""
        records = (
            cookie_records
            if cookie_records is not None
            else self._cookie_handler.get_httpx_cookie_records()
        )
        client.cookies.clear()
        for record in records:
            name = (record.get("name") or "").strip()
            if not name:
                continue
            value = record.get("value") or ""
            domain = (record.get("domain") or "").strip()
            path = (record.get("path") or "/").strip() or "/"
            if domain:
                client.cookies.set(name, value, domain=domain, path=path)
            else:
                client.cookies.set(name, value, path=path)

    # ── Multipart ─────────────────────────────────────────────────────────────

    def _build_multipart_files(self, request: Request) -> tuple[dict[str, Any] | None, list[Any]]:
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

        files: dict[str, Any] = {}
        opened_handles: list[Any] = []

        try:
            request_files = getattr(request, "files", {}) or {}
            for field, value in request_files.items():
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
        self, raw: httpx.Response, request: Request, elapsed: float, sent_headers: dict = None
    ) -> Response:
        # Explicitly read the body to ensure it's properly consumed
        body = raw.content
        logger.debug(
            "HttpxDispatcher._wrap_response: status=%d body_len=%d headers_count=%d",
            raw.status_code,
            len(body),
            len(raw.headers),
        )
        # sent_headers: the actual headers sent to httpx (including injected auth)
        return Response(
            status_code=raw.status_code,
            reason=self._extract_reason_phrase(raw),
            headers={str(k): str(v) for k, v in raw.headers.items()},
            body=body,
            elapsed=elapsed,
            request=request,
            timestamp=utc_now(),
            sent_headers=redact_headers(sent_headers),
            sent_url=str(raw.request.url) if getattr(raw, "request", None) is not None else None,
            connection_info=self._extract_connection_info(raw, request),
            set_cookie_headers=raw.headers.get_list("set-cookie"),
        )

    @staticmethod
    def _extract_tls_info_from_stream(stream: Any) -> dict[str, Any]:
        """Best-effort TLS/certificate extraction from a transport stream."""
        info: dict[str, Any] = {}
        if stream is None or not hasattr(stream, "get_extra_info"):
            return info

        try:
            ssl_obj = stream.get_extra_info("ssl_object")
        except Exception:
            ssl_obj = None

        if ssl_obj is None:
            return info

        try:
            info["tls_version"] = ssl_obj.version()
        except Exception:
            pass
        try:
            cipher = ssl_obj.cipher()
            if cipher:
                info["cipher"] = cipher[0]
                if len(cipher) > 2:
                    info["cipher_bits"] = cipher[2]
        except Exception:
            pass

        try:
            cert = ssl_obj.getpeercert()
        except Exception:
            cert = None

        if isinstance(cert, dict) and cert:
            subject = cert.get("subject") or []
            issuer = cert.get("issuer") or []

            def _first_name(parts: Any, key: str) -> str:
                try:
                    for item in parts:
                        for kv in item:
                            if isinstance(kv, tuple) and len(kv) == 2 and kv[0] == key:
                                return str(kv[1])
                except Exception:
                    return ""
                return ""

            info["cert_subject"] = _first_name(subject, "commonName")
            info["cert_issuer"] = _first_name(issuer, "commonName")
            if cert.get("notBefore"):
                info["cert_not_before"] = cert.get("notBefore")
            if cert.get("notAfter"):
                info["cert_not_after"] = cert.get("notAfter")
            if cert.get("serialNumber"):
                info["cert_serial"] = cert.get("serialNumber")
            san = cert.get("subjectAltName") or []
            if isinstance(san, (list, tuple)):
                info["cert_san_count"] = len(san)

        return info

    def _extract_connection_info(self, raw: httpx.Response, request: Request) -> dict[str, Any]:
        """Extract transport metadata (TLS/cert/peer) for response diagnostics."""
        follow_redirects = (
            request.follow_redirects
            if request.follow_redirects is not None
            else self._follow_redirects
        )
        info: dict[str, Any] = {
            "verify_ssl": bool(getattr(request, "verify_ssl", True)),
            "follow_redirects": bool(follow_redirects),
        }

        try:
            req_url = str(raw.request.url)
            info["sent_url"] = req_url
        except Exception:
            req_url = request.url
            info["sent_url"] = req_url

        stream = None
        try:
            ext = getattr(raw, "extensions", {}) or {}
            stream = ext.get("network_stream") or ext.get("stream")
        except Exception:
            stream = None

        info.update(self._extract_tls_info_from_stream(stream))

        if stream is not None and hasattr(stream, "get_extra_info"):
            try:
                peer = stream.get_extra_info("server_addr")
                if peer:
                    info["server_addr"] = str(peer)
            except Exception:
                pass

        return info

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
        chain = " → ".join(f"{r.status_code} {r.headers.get('location', '?')}" for r in raw.history)
        logger.info(
            "Request followed %d redirect(s): %s → %d",
            len(raw.history),
            chain,
            raw.status_code,
        )

    # ── Execute ───────────────────────────────────────────────────────────────

    def execute(
        self,
        request: Request,
        headers: dict[str, str],
        auth_headers: dict[str, str] | None = None,
    ) -> Response:
        """Send *request* via httpx and return a wrapped :class:`Response`.

        Auth headers are removed from *headers* and passed through httpx's
        native ``auth`` parameter so they survive cross-origin redirects (see
        :class:`~equinox.core.client.auth_redirect._RedirectSafeAuth`).
        """
        client = self._ensure_client(request.verify_ssl)

        logger.debug(
            "HttpxDispatcher: sending %s %s",
            request.method,
            Validator.sanitize_for_display(request.url, 100),
        )

        # Route auth headers through the redirect-safe adapter instead of
        # embedding them directly — httpx strips plain headers on redirects.
        httpx_auth: _RedirectSafeAuth | None = None
        # Make a copy of headers before popping auth headers for sent_headers
        sent_headers = dict(headers)
        if auth_headers:
            for key in auth_headers:
                headers.pop(key, None)
            httpx_auth = _RedirectSafeAuth(auth_headers)

        multipart_files, opened_handles = self._build_multipart_files(request)
        user_set_content_type = any(k.lower() == "content-type" for k in headers)

        start_time = time.perf_counter()
        raw: httpx.Response | None = None
        try:
            httpx_request = client.build_request(
                method=request.method,
                url=request.url,
                headers=headers,
                params=request.params,
                content=(
                    request.body.encode("utf-8") if (request.body and not multipart_files) else None
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

        if raw is None:
            raise RuntimeError("HttpxDispatcher.execute: transport returned no response")
        self._log_redirect_chain(raw)
        elapsed = time.perf_counter() - start_time
        logger.debug(
            "HttpxDispatcher: completed in %.3fs — status %d",
            elapsed,
            raw.status_code,
        )
        return self._wrap_response(raw, request, elapsed, sent_headers=sent_headers)

    # ── Dunder helpers ────────────────────────────────────────────────────────

    def __repr__(self) -> str:
        with self._client_lock:
            state = "open" if self._clients else "closed"
            client_count = len(self._clients)
        return (
            f"HttpxDispatcher(state={state!r}, timeout={self._timeout}, "
            f"verify_ssl={self._verify_ssl}, proxy={redact_url(self._proxy)!r}, clients={client_count})"
        )
