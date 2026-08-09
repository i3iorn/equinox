"""Display / render mixin for ResponsePanel.

Handles populating every tab with response data, applying syntax
highlighting, and formatting helpers.  Has no ``__init__`` — relies on
``self.*`` attributes set by ``ResponsePanel.__init__``.
"""

# mypy: disable-error-code=attr-defined
from __future__ import annotations

import difflib
import json
import logging
from typing import Any

from equinox.core import urls
from equinox.core.request import Response
from equinox.gui.response_panel._formatting import format_size
from equinox.gui.response_panel._formatting import parse_cookies
from equinox.gui.response_panel.pretty_print import CT_HIGHLIGHTERS
from equinox.gui.response_panel.pretty_print import PrettyPrintRunnable
from equinox.gui.theme import Colors
from equinox.security import redact_body
from equinox.security import redact_headers
from equinox.security import redact_url
from PyQt6.QtWidgets import QTableWidgetItem

logger = logging.getLogger(__name__)

_BINARY_CONTENT_TYPE_TOKENS = (
    "application/octet-stream",
    "application/pdf",
    "image/",
    "audio/",
    "video/",
    "font/",
)

_TEXT_CONTENT_TYPE_HINTS = (
    "json",
    "xml",
    "html",
    "text",
    "javascript",
    "x-www-form-urlencoded",
    "yaml",
    "csv",
)


class ResponseDisplayMixin:
    """Mixin providing all *display* methods for ResponsePanel."""

    _body_highlighter: Any

    # ------------------------------------------------------------------
    # Status Bar
    # ------------------------------------------------------------------

    def _update_status_bar(self, response: Response) -> None:
        """Update status bar with response code, elapsed time, and size."""
        code = response.status_code
        color = self._get_status_color(code)
        content_type = str(response.headers.get("content-type", "")).strip()
        content_type_summary = content_type.split(";", 1)[0] if content_type else "Unknown type"

        # Log widget references to ensure they exist
        logger.debug(
            "_update_status_bar: status_label=%s time_label=%s size_label=%s",
            type(self.status_label).__name__,
            type(self.time_label).__name__,
            type(self.size_label).__name__,
        )

        self.status_label.setText(f"{code}  {response.reason}")
        self.status_label.setStyleSheet(f"color: {color};")
        self.time_label.setText(f"{int(response.elapsed * 1000)} ms")
        self.size_label.setText(format_size(response.size))
        self.content_type_label.setText(content_type_summary)
        self.content_type_label.setToolTip(
            content_type or "Content type not provided by the server",
        )

        # Verify values were actually set
        logger.debug(
            "_update_status_bar: set status_label text=%r time_label text=%r size_label text=%r",
            self.status_label.text(),
            self.time_label.text(),
            self.size_label.text(),
        )

        logger.debug(
            "_update_status_bar: %d %s, %d ms, %s",
            code,
            response.reason,
            int(response.elapsed * 1000),
            format_size(response.size),
        )

    @staticmethod
    def _get_status_color(status_code: int) -> str:
        """Get color for HTTP status code."""
        if status_code < 300:
            return str(Colors.GREEN)
        elif status_code < 400:
            return str(Colors.AMBER)
        else:
            return str(Colors.RED)

    # ------------------------------------------------------------------
    # Body Rendering
    # ------------------------------------------------------------------

    def _display_body(self, response: Response) -> None:
        """Display response body, handling size warnings."""
        logger.debug(
            "_display_body: size=%d, threshold=%d, body_text=%s visible=%s",
            response.size,
            self._LARGE_BODY_THRESHOLD,
            type(self.body_text).__name__,
            self.body_text.isVisible() if hasattr(self.body_text, "isVisible") else "N/A",
        )
        if response.size > self._LARGE_BODY_THRESHOLD:
            self._body_warning.setVisible(True)
            if response.size > self._MAX_RENDER_BODY_SIZE:
                self._body_warn_label.setText(
                    f"Response body is {format_size(response.size)} — full render disabled above {format_size(self._MAX_RENDER_BODY_SIZE)}.",
                )
                self._body_load_btn.setEnabled(False)
                self._body_load_btn.setToolTip("Use Download to inspect very large payloads safely")
                self.body_text.setPlaceholderText(
                    "Payload too large to render fully. Click 'Load Full' to preview only the first chunk.",
                )
            else:
                self._body_warn_label.setText(
                    f"Response body is {format_size(response.size)} — rendering may be slow.",
                )
                self._body_load_btn.setEnabled(True)
                self._body_load_btn.setToolTip("")
                self.body_text.setPlaceholderText("Click 'Load Full' to display the body.")
            self.body_text.clear()
            self._raw_body_text = ""
            self._pretty_body_text = ""
            logger.debug("_display_body: large body — deferred rendering")
        else:
            self._body_warning.setVisible(False)
            self._body_load_btn.setEnabled(True)
            self._body_load_btn.setToolTip("")
            # Pretty-printing (JSON parse + re-serialize, or an XML DOM
            # pretty-print) ran synchronously on the UI thread here, which
            # could noticeably stall the GUI for a complex body well under
            # the "large body" threshold. Route through the same off-thread
            # PrettyPrintRunnable already used by the "Load Full" flow
            # (_load_large_body/_on_pretty_result in actions_mixin.py)
            # instead of duplicating that formatting inline.
            self._loading_label.setVisible(True)
            marker = self._get_current_request_marker()
            runnable = PrettyPrintRunnable(response, marker)
            runnable.signals.result.connect(self._on_pretty_result)
            self._thread_pool.start(runnable)
            logger.debug("_display_body: dispatched off-thread pretty-print")

    @staticmethod
    def _decode_response_body(response: Response) -> str:
        """Decode response bytes to text for raw readability mode."""
        try:
            body = getattr(response, "body", b"")
            content_type = str(getattr(response, "headers", {}).get("content-type", "")).lower()

            if isinstance(body, (bytes, bytearray)):
                raw = bytes(body)
                if ResponseDisplayMixin._looks_binary_payload(raw, content_type):
                    ctype = content_type or "unknown"
                    return f"[Binary response omitted in text view: {len(raw)} bytes, content-type={ctype}]"

                # Prefer the Response.text decoding path because it honors charset.
                text_value = getattr(response, "text", None)
                if isinstance(text_value, str):
                    return text_value
                return raw.decode("utf-8", errors="replace")
            return str(response.body)
        except Exception:
            logger.exception("Failed decoding response body", exc_info=True)
            return ""

    @staticmethod
    def _looks_binary_payload(body: bytes, content_type: str) -> bool:
        """Best-effort check for binary payloads that should not be text-rendered."""
        ct = (content_type or "").lower()
        if any(token in ct for token in _BINARY_CONTENT_TYPE_TOKENS):
            return True
        if any(token in ct for token in _TEXT_CONTENT_TYPE_HINTS):
            return False
        # Null bytes in payload strongly indicate non-text content.
        return b"\x00" in body

    def _render_body_by_mode(self, mode: str) -> None:
        """Render cached body text in the requested readability mode."""
        raw = self._maybe_redact_text(getattr(self, "_raw_body_text", "") or "")
        pretty = self._maybe_redact_text(getattr(self, "_pretty_body_text", "") or raw)
        mode = (mode or "pretty").lower()

        if mode == "raw":
            text = raw
        elif mode == "split":
            text = "=== Pretty ===\n" + pretty + "\n\n=== Raw ===\n" + raw
        elif mode == "diff":
            diff_lines = difflib.unified_diff(
                raw.splitlines(keepends=True),
                pretty.splitlines(keepends=True),
                fromfile="raw",
                tofile="pretty",
                lineterm="",
            )
            text = "".join(diff_lines) or "(No differences between raw and pretty views)"
        else:
            text = pretty

        self.body_text.set_code(text)

    def _maybe_redact_text(self, text: str) -> str:
        """Return redacted text when preview mode is enabled."""
        if not getattr(self, "_redaction_preview", False):
            return text
        try:
            return redact_body(text) or ""
        except Exception:
            logger.exception("Redacting body preview failed", exc_info=True)
            return text

    def _maybe_redact_headers(self, headers: dict[str, str]) -> dict[str, str]:
        """Return headers with sensitive values masked when redaction preview is enabled."""
        if not getattr(self, "_redaction_preview", False):
            return dict(headers)
        try:
            masked = redact_headers(dict(headers))
            # Ensure non-sensitive values still pass body-level redaction for embedded tokens.
            return {
                k: (v if v == "[REDACTED]" else self._maybe_redact_text(str(v)))
                for k, v in masked.items()
            }
        except Exception:
            logger.exception("Redacting headers preview failed", exc_info=True)
            return dict(headers)

    def _maybe_redact_url(self, value: str) -> str:
        """Redact sensitive URL components when preview mode is enabled."""
        if not getattr(self, "_redaction_preview", False):
            return value
        try:
            return redact_url(value) or value
        except Exception:
            logger.exception("Redacting URL preview failed", exc_info=True)
            return value

    # ------------------------------------------------------------------
    # JSON Tree
    # ------------------------------------------------------------------

    def _display_json_tree(self, response: Response) -> None:
        try:
            can_show_json = bool(response.is_json and response.size <= self._LARGE_BODY_THRESHOLD)
            logger.debug(
                "_display_json_tree: is_json=%s, can_show=%s",
                getattr(response, "is_json", None),
                can_show_json,
            )
            if can_show_json:
                obj = response.json()
                defer_tree = self.tabs.currentIndex() != self._json_tab_idx
                self._json_tree.load_json(obj, defer=defer_tree)
                self._search_bar.set_json_doc(obj)
            else:
                self._json_tree.clear()
                self._search_bar.set_json_doc(None)

            self.tabs.setTabVisible(self._json_tab_idx, can_show_json)
            self._view_json_act.setEnabled(can_show_json)
        except Exception:
            logger.exception("_display_json_tree: failed")
            self._json_tree.clear()
            self._search_bar.set_json_doc(None)
            self.tabs.setTabVisible(self._json_tab_idx, False)
            self._view_json_act.setEnabled(False)

    # ------------------------------------------------------------------
    # Headers
    # ------------------------------------------------------------------

    def _display_headers(self, response: Response) -> None:
        self._hdrs_search.blockSignals(True)
        self._hdrs_search.clear()
        self._hdrs_search.blockSignals(False)

        logger.debug(
            "_display_headers: resp_headers_table=%s visible=%s tabs=%s",
            type(self.resp_headers_table).__name__,
            self.resp_headers_table.isVisible()
            if hasattr(self.resp_headers_table, "isVisible")
            else "N/A",
            type(self.tabs).__name__,
        )

        self.resp_headers_table.load(self._maybe_redact_headers(dict(response.headers)))
        self._total_header_count = len(response.headers)
        self._update_headers_count_label()
        logger.debug(
            "_display_headers: %d headers loaded, tabs visible=%s",
            self._total_header_count,
            self.tabs.isVisible(),
        )

    def _on_hdrs_filter_changed(self, text: str) -> None:
        """Filter headers table by substring match on name/value."""
        self.resp_headers_table.filter(text)
        self._update_headers_count_label(self.resp_headers_table.rowCount())

    def _update_headers_count_label(self, visible_count: int | None = None) -> None:
        """Show both filtered and total header counts when a filter is active."""
        total_count = int(getattr(self, "_total_header_count", 0) or 0)
        shown_count = (
            self.resp_headers_table.rowCount() if visible_count is None else int(visible_count)
        )

        if total_count <= 0:
            self._hdrs_count_label.setText("No headers")
            return
        if shown_count >= total_count:
            suffix = "header" if total_count == 1 else "headers"
            self._hdrs_count_label.setText(f"{total_count} {suffix}")
            return
        self._hdrs_count_label.setText(f"{shown_count} of {total_count} shown")

    # ------------------------------------------------------------------
    # Timings
    # ------------------------------------------------------------------

    def _display_timings(self, response: Response) -> None:
        self._timings_toggle.setChecked(False)
        self._timings_toggle.setText("▶ Timings")
        self._timings_label.setVisible(False)

        timings = getattr(response, "timings", None)
        if not timings:
            self._timings_toggle.setVisible(False)
            self._timings_label.setVisible(False)
            return

        total = timings.get("total_ms", int(response.elapsed * 1000))
        parts = [f"Total: {total} ms"]
        for key in ("dns_ms", "connect_ms", "tls_ms", "ttfb_ms", "transfer_ms"):
            if key in timings:
                label = key.replace("_ms", "").replace("ttfb", "TTFB").upper()
                parts.append(f"{label}: {timings[key]} ms")

        self._timings_label.setText("  ·  ".join(parts))
        self._timings_toggle.setVisible(True)

    def _on_timings_toggled(self, checked: bool) -> None:
        """Show/hide the timing breakdown label."""
        self._timings_toggle.setText("▼ Timings" if checked else "▶ Timings")
        self._timings_label.setVisible(checked)

    # ------------------------------------------------------------------
    # Cookies
    # ------------------------------------------------------------------

    def _load_cookies_tab(self, headers: dict[str, str]) -> None:
        """Parse Set-Cookie headers and populate the Cookies table."""
        self._cookies_table.setRowCount(0)
        cookies = parse_cookies(headers)
        for cookie_name, attributes in cookies:
            self._add_cookie_row(cookie_name, attributes)
        self._update_cookies_tab_title()

    def _add_cookie_row(self, cookie_name: str, attributes: dict[str, str]) -> None:
        """Add a single cookie row to the table."""
        row = self._cookies_table.rowCount()
        self._cookies_table.insertRow(row)

        self._cookies_table.setItem(row, 0, QTableWidgetItem(cookie_name))
        self._cookies_table.setItem(row, 1, QTableWidgetItem(attributes.get("value", "")))
        self._cookies_table.setItem(row, 2, QTableWidgetItem(attributes.get("domain", "")))
        self._cookies_table.setItem(row, 3, QTableWidgetItem(attributes.get("path", "")))
        self._cookies_table.setItem(row, 4, QTableWidgetItem(attributes.get("expires", "")))
        self._cookies_table.setItem(
            row,
            5,
            QTableWidgetItem("✓" if attributes.get("secure") == "true" else ""),
        )
        self._cookies_table.setItem(
            row,
            6,
            QTableWidgetItem("✓" if attributes.get("httponly") == "true" else ""),
        )

    def _update_cookies_tab_title(self) -> None:
        """Update the cookies tab title with the cookie count."""
        count = self._cookies_table.rowCount()
        for i in range(self.tabs.count()):
            tab_text = self.tabs.tabText(i)
            if tab_text.startswith("Cookies"):
                new_text = f"Cookies ({count})" if count else "Cookies"
                self.tabs.setTabText(i, new_text)
                break

    # ------------------------------------------------------------------
    # Sent Request
    # ------------------------------------------------------------------

    def _display_sent_request(self, response: Response) -> None:
        """Populate the 'Sent Request' tab from the response's metadata."""
        logger.debug(
            "_display_sent_request: method=%s url=%s",
            response.request.method,
            getattr(response, "sent_url", None) or response.request.url,
        )
        self._display_sent_request_method(response.request.method)
        self._display_sent_request_url(response)
        self._display_sent_request_headers(response)
        self._display_sent_request_body(response.request.body)

    def _display_sent_request_method(self, method: str) -> None:
        """Display method badge with color."""
        color = Colors.METHOD.get(method, Colors.FG)
        self.sent_method_label.setText(f" {method} ")
        self.sent_method_label.setStyleSheet(f"color: {color};")

    def _display_sent_request_url(self, response: Response) -> None:
        """Display the URL that was sent (prefer expanded httpx URL)."""
        display_url = self._build_display_url(response)
        self.sent_url_label.setText(self._maybe_redact_url(display_url))

    @staticmethod
    def _build_display_url(response: Response) -> str:
        """Build the display URL from sent_url or request URL with params."""
        if response.sent_url:
            return str(response.sent_url)

        req = response.request
        params = getattr(req, "params", None)
        if params:
            return str(urls.append_query_params(req.url, params, merge_existing=False))

        return str(req.url)

    def _display_sent_request_headers(self, response: Response) -> None:
        """Display headers sent (prefer sent_headers which include auth)."""
        req = response.request
        sent_hdrs = response.sent_headers or req.headers or {}
        self.sent_headers_table.load(self._maybe_redact_headers(dict(sent_hdrs)))

    def _display_sent_request_body(self, body: Any | None) -> None:
        """Display the request body, if present."""
        if body:
            self.sent_body_text.set_code(self._maybe_redact_text(self._format_request_body(body)))
        else:
            self.sent_body_text.setPlaceholderText("(no body)")
            self.sent_body_text.clear()

    def _display_connection_details(self, response: Response) -> None:
        """Populate connection tab with transport and TLS-related metadata."""
        url = getattr(response, "sent_url", None) or response.request.url
        parts = urls.url_metadata(url or "")
        scheme = str(parts.get("scheme") or "").lower()
        is_https = scheme == "https"
        meta = getattr(response, "connection_info", None) or {}

        lines = [
            f"URL: {self._maybe_redact_url(url)}",
            f"Host: {parts.get('netloc') or '(unknown)'}",
            f"Transport: {'HTTPS' if is_https else 'HTTP'}",
            f"Verify SSL: {bool(meta.get('verify_ssl', getattr(response.request, 'verify_ssl', True)))}",
            f"Follow redirects: {bool(meta.get('follow_redirects', getattr(response.request, 'follow_redirects', True)))}",
        ]

        timings = getattr(response, "timings", None) or {}
        if "tls_ms" in timings:
            lines.append(f"TLS handshake: {timings.get('tls_ms')} ms")

        hsts = response.headers.get("strict-transport-security", "")
        lines.append(f"HSTS: {'present' if hsts else 'missing'}")
        if hsts:
            lines.append(f"HSTS value: {hsts}")

        tls_version = meta.get("tls_version")
        if tls_version:
            lines.append(f"TLS version: {tls_version}")

        cipher = meta.get("cipher")
        if cipher:
            bits = meta.get("cipher_bits")
            if bits:
                lines.append(f"Cipher: {cipher} ({bits} bits)")
            else:
                lines.append(f"Cipher: {cipher}")

        cert_subject = meta.get("cert_subject")
        cert_issuer = meta.get("cert_issuer")
        cert_not_after = meta.get("cert_not_after")
        cert_not_before = meta.get("cert_not_before")
        cert_serial = meta.get("cert_serial")
        cert_san_count = meta.get("cert_san_count")

        if cert_subject or cert_issuer or cert_not_after:
            lines.append("Certificate details:")
            if cert_subject:
                lines.append(f"  Subject CN: {cert_subject}")
            if cert_issuer:
                lines.append(f"  Issuer CN: {cert_issuer}")
            if cert_not_before:
                lines.append(f"  Valid from: {cert_not_before}")
            if cert_not_after:
                lines.append(f"  Valid to: {cert_not_after}")
            if cert_serial:
                lines.append(f"  Serial: {cert_serial}")
            if cert_san_count is not None:
                lines.append(f"  SAN entries: {cert_san_count}")
        else:
            lines.append("Certificate details: not available from current transport metadata")

        server_addr = meta.get("server_addr")
        if server_addr:
            lines.append(f"Server address: {server_addr}")
        self.connection_text.set_code("\n".join(lines))

    @staticmethod
    def _format_request_body(body: Any) -> str:
        """Format request body as display string (try JSON first)."""
        try:
            if isinstance(body, (dict, list)):
                return json.dumps(body, indent=2, ensure_ascii=False)
            if isinstance(body, (bytes, bytearray)):
                body = body.decode("utf-8", errors="replace")
            if isinstance(body, str):
                return json.dumps(json.loads(body), indent=2, ensure_ascii=False)
        except Exception:
            pass

        return body if isinstance(body, str) else str(body)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _apply_highlighter(self, content_type: str) -> None:
        """Apply syntax highlighter based on content-type."""
        if self._body_highlighter is not None:
            self._body_highlighter.setDocument(None)
            self._body_highlighter = None

        ct = (content_type or "").lower()
        cls = next((h for token, h in CT_HIGHLIGHTERS if token in ct), None)
        if cls is None:
            logger.debug("_apply_highlighter: no highlighter for ct=%r", content_type)
            return

        doc = self.body_text.document()
        try:
            self._body_highlighter = cls(doc)
            logger.debug("_apply_highlighter: attached %s for ct=%r", cls.__name__, content_type)
        except Exception:
            logger.exception(
                "Failed to create highlighter for content-type=%s; skipping highlighting",
                content_type,
            )
            if self._body_highlighter is not None:
                try:
                    self._body_highlighter.setDocument(None)
                except Exception:
                    pass
            self._body_highlighter = None
