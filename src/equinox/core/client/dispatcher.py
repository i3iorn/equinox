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
from typing import IO, Any

import httpx
from equinox.core.client.auth_redirect import _RedirectSafeAuth
from equinox.core.client.cookie_handler import CookieHandler
from equinox.core.exceptions import RequestTimeoutError
from equinox.core.exceptions import ValidationError
from equinox.core.io.multipart import build_multipart_files
from equinox.core.request import Request
from equinox.core.request import Response
from equinox.core.util.time import utc_now
from equinox.core.validation import Sanitizer, Validator
from equinox.security import redact_headers
from equinox.security import redact_url

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
                # The pipeline validates the URL the user supplied, but httpx
                # follows redirects internally. Request hooks fire for every
                # hop, so re-validating there closes the redirect SSRF hole.
                event_hooks={"request": [self._validate_outbound_request]},
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

    @staticmethod
    def _validate_outbound_request(request: httpx.Request) -> None:
        """Re-run SSRF validation for every outbound hop, redirects included.

        ``HTTPClient._validate_request`` only sees the URL the caller supplied.
        With automatic redirects httpx issues further requests that never pass
        through it, so a public host could bounce the client to
        ``169.254.169.254`` or another internal address. httpx invokes request
        hooks once per hop, which makes this the correct interception point.
        """
        Validator.validate_resolved_url(str(request.url))

    # ── Cookie sync ───────────────────────────────────────────────────────────

    def flush_cookies(self, response: Response, url: str) -> None:
        """Update the cookie jar from *response*; skip rebuild if no Set-Cookie headers."""
        updated = self._cookie_handler.update_from_response(response, url)
        if updated:
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

    def _build_multipart_files(
        self,
        request: Request,
    ) -> tuple[dict[str, Any] | list[tuple[str, Any]] | None, list[Any]]:
        """Build httpx-compatible multipart files for *request*.

        Two input shapes are supported::

            request.files            legacy dict of ``field -> path/tuple``
                                     entries (programmatic/test callers)
            request.multipart_data   list of ``{key, type, value}`` dicts as
                                     produced by the GUI/application layer,
                                     converted via
                                     :func:`equinox.core.io.multipart.build_multipart_files`

        Returns:
            A ``(files, opened_handles)`` tuple.  ``files`` is ``None`` when
            the request carries no uploads.  The caller is responsible for
            closing every handle in *opened_handles*.
        """
        request_files = getattr(request, "files", None) or {}
        if not request_files:
            # Production requests carry uploads in ``multipart_data``; the
            # legacy ``files`` attribute does not exist on ``Request``.
            multipart_data = getattr(request, "multipart_data", None)
            if not multipart_data:
                return None, []
            return build_multipart_files(multipart_data)

        files: dict[str, Any] = {}
        opened_handles: list[IO[bytes]] = []

        for field, value in request_files.items():
            if isinstance(value, (str, Path)):
                Validator.validate_file_path(str(value))
                with Path(value).open("rb") as fh:
                    opened_handles.append(fh)
                    files[field] = (Path(value).name, fh)
            elif isinstance(value, tuple) and len(value) in (2, 3):
                files[field] = value
            else:
                raise ValidationError(
                    f"Unsupported file spec for field {field!r}: "
                    f"expected a path or (filename, data[, content_type]) tuple",
                )

        return files, opened_handles

    # ── Response helpers ──────────────────────────────────────────────────────

    def _wrap_response(
        self,
        raw: httpx.Response,
        request: Request,
        elapsed: float,
        sent_headers: dict[str, str] | None = None,
    ) -> Response:
        body = raw.content
        logger.debug(
            "HttpxDispatcher._wrap_response: status=%d body_len=%d hdrs=%d",
            raw.status_code,
            len(body),
            len(raw.headers),
        )
        # sent_headers: the actual headers sent to httpx (including injected auth)
        headers_to_redact: dict[str, Any] = (
            {str(k): v for k, v in sent_headers.items()} if sent_headers is not None else {}
        )
        redacted_sent_headers = {
            str(k): str(v)
            for k, v in (redact_headers(headers_to_redact) or {}).items()
            if v is not None
        }
        return Response(
            status_code=raw.status_code,
            reason=self._extract_reason_phrase(raw),
            headers={str(k): str(v) for k, v in raw.headers.items()},
            body=body,
            elapsed=elapsed,
            request=request,
            timestamp=utc_now(),
            sent_headers=redacted_sent_headers,
            sent_url=redact_url(str(raw.request.url))
            if getattr(raw, "request", None) is not None
            else None,
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
            info["sent_url"] = redact_url(str(raw.request.url))
        except Exception:
            info["sent_url"] = redact_url(request.url)

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
        if logger.isEnabledFor(logging.INFO):
            hops = " → ".join(
                f"{r.status_code} {redact_url(r.headers.get('location', '?')) or '?'}"
                for r in raw.history
            )
            logger.info("Request followed %d redirect(s): %s", len(raw.history), hops)

    # ── Execute ───────────────────────────────────────────────────────────────

    def execute(
        self,
        request: Request,
        headers: dict[str, str],
        auth_headers: dict[str, str] | None = None,
    ) -> Response:
        """Send request via httpx and return a wrapped Response."""
        client = self._ensure_client(request.verify_ssl)
        self._log_request_start(request)

        sent_headers = dict(headers)
        httpx_auth = self._extract_auth_headers(headers, auth_headers)

        multipart_files, opened_handles = self._build_multipart_files(request)
        user_set_content_type = self._user_set_content_type(headers)

        start_time = time.perf_counter()
        raw_response = None

        try:
            httpx_request = self._build_httpx_request(
                client,
                request,
                headers,
                multipart_files,
                user_set_content_type,
            )
            raw_response = self._send_httpx_request(
                client,
                httpx_request,
                request,
                httpx_auth,
            )
        finally:
            self._close_file_handles(opened_handles)

        if raw_response is None:
            raise RuntimeError("HttpxDispatcher.execute: transport returned no response")

        self._log_redirect_chain(raw_response)
        elapsed = time.perf_counter() - start_time
        self._log_request_complete(raw_response, elapsed)

        return self._wrap_response(
            raw_response,
            request,
            elapsed,
            sent_headers=sent_headers,
        )

    def _log_request_start(self, request: Request) -> None:
        logger.debug(
            "HttpxDispatcher: sending %s %s",
            request.method,
            Sanitizer.sanitize_for_display(request.url, 100),
        )

    def _log_request_complete(self, response: httpx.Response, elapsed: float) -> None:
        logger.debug(
            "HttpxDispatcher: completed in %.3fs — status %d",
            elapsed,
            response.status_code,
        )

    def _extract_auth_headers(
        self,
        headers: dict[str, str],
        auth_headers: dict[str, str] | None,
    ) -> _RedirectSafeAuth | None:
        if not auth_headers:
            return None

        for key in auth_headers:
            headers.pop(key, None)

        return _RedirectSafeAuth(auth_headers)

    def _user_set_content_type(self, headers: dict[str, str]) -> bool:
        return any(k.lower() == "content-type" for k in headers)

    def _build_httpx_request(
        self,
        client: httpx.Client,
        request: Request,
        headers: dict[str, str],
        multipart_files: dict[str, Any] | list[tuple[str, Any]] | None,
        user_set_content_type: bool,
    ) -> httpx.Request:
        content = None
        if request.body and not multipart_files:
            if isinstance(request.body, str):
                # Reuse cached encoding if the body object hasn't changed.
                if request._encoded_body_ref is not request.body:
                    request._encoded_body = request.body.encode("utf-8")
                    request._encoded_body_ref = request.body
                content = request._encoded_body
            else:
                content = request.body

        httpx_request = client.build_request(
            method=request.method,
            url=request.url,
            headers=headers,
            params=request.params,
            content=content,
            files=multipart_files or None,
            timeout=request.timeout if request.timeout is not None else self._timeout,
        )

        self._strip_auto_content_type(
            httpx_request,
            user_set_content_type,
            bool(multipart_files),
        )

        return httpx_request

    def _send_httpx_request(
        self,
        client: httpx.Client,
        httpx_request: httpx.Request,
        request: Request,
        httpx_auth: _RedirectSafeAuth | None,
    ) -> httpx.Response:
        try:
            return client.send(
                httpx_request,
                follow_redirects=(
                    request.follow_redirects
                    if request.follow_redirects is not None
                    else self._follow_redirects
                ),
                auth=httpx_auth,
            )
        except httpx.TimeoutException as exc:
            # Translate raw httpx timeouts into the domain error so the retry
            # loop in ``RetryPolicy.execute`` (which wraps this dispatch) can
            # act on them; unmapped, they would bypass retries entirely.
            url = redact_url(request.url) or "unknown URL"
            message = {
                httpx.ConnectTimeout: "Connection timed out",
                httpx.ReadTimeout: "Server response timed out",
            }.get(type(exc), "Request timed out")
            raise RequestTimeoutError(
                message,
                details={"url": url},
                hint_key="timeout",
            ) from exc

    def _close_file_handles(self, handles: list[IO[bytes]]) -> None:
        for fh in handles:
            try:
                fh.close()
            except Exception as exc:
                logger.debug("Failed to close file handle: %s", exc)

    # ── Dunder helpers ────────────────────────────────────────────────────────

    def __repr__(self) -> str:
        with self._client_lock:
            state = "open" if self._clients else "closed"
        return (
            f"HttpxDispatcher(state={state!r}, timeout={self._timeout}, "
            f"verify_ssl={self._verify_ssl}, proxy={redact_url(self._proxy)!r}, "
            f"clients={len(self._clients)})"
        )
