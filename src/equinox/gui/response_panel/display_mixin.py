"""Display / render mixin for ResponsePanel.

Handles populating every tab with response data, applying syntax
highlighting, and formatting helpers.  Has no ``__init__`` — relies on
``self.*`` attributes set by ``ResponsePanel.__init__``.
"""

from __future__ import annotations

import http.cookies as _hc
import json
import logging
from typing import Dict, Any
from urllib.parse import urlencode

from PyQt6.QtWidgets import QTableWidgetItem

from equinox.core.request import Response
from equinox.gui.response_panel.pretty_print import CT_HIGHLIGHTERS
from equinox.gui.theme import Colors

logger = logging.getLogger(__name__)


class ResponseDisplayMixin:
    """Mixin providing all *display* methods for ResponsePanel."""

    # ------------------------------------------------------------------
    # Status Bar
    # ------------------------------------------------------------------

    def _update_status_bar(self, response: Response) -> None:
        code = response.status_code
        if code < 300:
            color = Colors.GREEN
        elif code < 400:
            color = Colors.AMBER
        else:
            color = Colors.RED

        self.status_label.setText(f"{code}  {response.reason}")
        self.status_label.setStyleSheet(f"font-weight: bold; color: {color};")
        self.time_label.setText(f"{int(response.elapsed * 1000)} ms")
        self.size_label.setText(self._format_size(response.size))

    # ------------------------------------------------------------------
    # Body Rendering
    # ------------------------------------------------------------------

    def _display_body(self, response: Response) -> None:
        if response.size > self._LARGE_BODY_THRESHOLD:
            self._body_warn_label.setText(
                f"Response body is {self._format_size(response.size)} — rendering may be slow."
            )
            self._body_warning.setVisible(True)
            self.body_text.setPlaceholderText("Click 'Load Full' to display the body.")
            self.body_text.clear()
        else:
            self._body_warning.setVisible(False)
            self.body_text.set_code(self._pretty_body(response))

    def _pretty_body(self, response: Response) -> str:
        """Pretty-print JSON or XML if possible."""
        try:
            if getattr(response, "is_json", False):
                return json.dumps(response.json(), indent=2, ensure_ascii=False)
        except Exception:
            pass

        ct = response.headers.get("content-type", "").lower()
        if any(x in ct for x in ("xml", "html", "svg")):
            try:
                import xml.dom.minidom
                return xml.dom.minidom.parseString(
                    response.text.encode()
                ).toprettyxml(indent="  ")
            except Exception:
                pass

        return response.text

    # ------------------------------------------------------------------
    # JSON Tree
    # ------------------------------------------------------------------

    def _display_json_tree(self, response: Response) -> None:
        try:
            can_show_json = bool(response.is_json and response.size <= self._LARGE_BODY_THRESHOLD)
            if can_show_json:
                obj = response.json()
                self._json_tree.load_json(obj)
                self._search_bar.set_json_doc(obj)
            else:
                self._json_tree.clear()
                self._search_bar.set_json_doc(None)

            self.tabs.setTabVisible(self._json_tab_idx, can_show_json)
            self._view_json_act.setEnabled(can_show_json)
        except Exception:
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

        self.resp_headers_table.load(response.headers)
        self._hdrs_count_label.setText(str(len(response.headers)))

    def _on_hdrs_filter_changed(self, text: str) -> None:
        """Filter headers table by substring match on name/value."""
        self.resp_headers_table.filter(text)
        self._hdrs_count_label.setText(str(self.resp_headers_table.rowCount()))

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

    def _load_cookies_tab(self, headers: Dict[str, str]) -> None:
        """Parse Set-Cookie headers and populate the Cookies table."""
        self._cookies_table.setRowCount(0)
        for key, value in headers.items():
            if key.lower() != "set-cookie":
                continue
            try:
                m = _hc.SimpleCookie()
                m.load(value)
                for cookie_name, morsel in m.items():
                    row = self._cookies_table.rowCount()
                    self._cookies_table.insertRow(row)
                    self._cookies_table.setItem(row, 0, QTableWidgetItem(cookie_name))
                    self._cookies_table.setItem(row, 1, QTableWidgetItem(morsel.value))
                    self._cookies_table.setItem(row, 2, QTableWidgetItem(morsel["domain"]))
                    self._cookies_table.setItem(row, 3, QTableWidgetItem(morsel["path"]))
                    self._cookies_table.setItem(row, 4, QTableWidgetItem(morsel["expires"]))
                    self._cookies_table.setItem(row, 5, QTableWidgetItem("✓" if morsel["secure"] else ""))
                    self._cookies_table.setItem(row, 6, QTableWidgetItem("✓" if morsel["httponly"] else ""))
            except Exception:
                # If parsing fails, add a raw row
                row = self._cookies_table.rowCount()
                self._cookies_table.insertRow(row)
                self._cookies_table.setItem(row, 0, QTableWidgetItem("(raw)"))
                self._cookies_table.setItem(row, 1, QTableWidgetItem(value))

        # Update tab title with count
        count = self._cookies_table.rowCount()
        for i in range(self.tabs.count()):
            if self.tabs.tabText(i).startswith("Cookies"):
                self.tabs.setTabText(i, f"Cookies ({count})" if count else "Cookies")
                break

    # ------------------------------------------------------------------
    # Sent Request
    # ------------------------------------------------------------------

    def _display_sent_request(self, response: Response) -> None:
        """Populate the 'Sent Request' tab from the response's metadata."""
        req = response.request

        # Method label colour
        color = Colors.METHOD.get(req.method, Colors.FG)
        self.sent_method_label.setText(f" {req.method} ")
        self.sent_method_label.setStyleSheet(
            f"font-weight: bold; color: white; "
            f"background: {color}; padding: 2px 8px; border-radius: 3px;"
        )

        # URL — prefer the fully-expanded URL httpx used (with params encoded)
        display_url = response.sent_url or req.url
        if not response.sent_url and getattr(req, "params", None):
            display_url = f"{req.url}?{urlencode(req.params)}"
        self.sent_url_label.setText(display_url)

        # Headers — prefer sent_headers (includes auth); fall back to req.headers
        sent_hdrs = response.sent_headers or req.headers or {}
        self.sent_headers_table.load(sent_hdrs)

        # Body
        if req.body:
            self.sent_body_text.set_code(self._try_pretty_json(req.body))
        else:
            self.sent_body_text.setPlaceholderText("(no body)")
            self.sent_body_text.clear()

    def _try_pretty_json(self, body: Any) -> str:
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
    # Highlighter
    # ------------------------------------------------------------------

    def _apply_highlighter(self, content_type: str) -> None:
        """Apply syntax highlighter based on content-type."""
        if self._body_highlighter is not None:
            self._body_highlighter.setDocument(None)
            self._body_highlighter = None

        ct = (content_type or "").lower()
        cls = next((h for token, h in CT_HIGHLIGHTERS if token in ct), None)
        if cls is None:
            return

        doc = self.body_text.document()
        try:
            self._body_highlighter = cls(doc)
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

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _format_size(size: int) -> str:
        """Human-readable size."""
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024 or unit == "GB":
                return f"{size:.1f} {unit}" if unit != "B" else f"{size} {unit}"
            size /= 1024.0
        return f"{size:.1f} GB"

