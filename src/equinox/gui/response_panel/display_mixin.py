"""Display / render mixin for ResponsePanel.

Handles populating every tab with response data, applying syntax
highlighting, and formatting helpers.  Has no ``__init__`` — relies on
``self.*`` attributes set by ``ResponsePanel.__init__``.
"""

from __future__ import annotations

import json
import logging
from typing import Dict, Any, Optional
from urllib.parse import urlencode

from PyQt6.QtWidgets import QTableWidgetItem

from equinox.core.request import Response
from equinox.gui.response_panel.pretty_print import CT_HIGHLIGHTERS
from equinox.gui.response_panel._formatting import (
    format_size,
    pretty_print_body,
    parse_cookies,
)
from equinox.gui.theme import Colors

logger = logging.getLogger(__name__)


class ResponseDisplayMixin:
    """Mixin providing all *display* methods for ResponsePanel."""

    # ------------------------------------------------------------------
    # Status Bar
    # ------------------------------------------------------------------

    def _update_status_bar(self, response: Response) -> None:
        """Update status bar with response code, elapsed time, and size."""
        code = response.status_code
        color = self._get_status_color(code)

        # Log widget references to ensure they exist
        logger.debug(
            "_update_status_bar: status_label=%s time_label=%s size_label=%s",
            type(self.status_label).__name__,
            type(self.time_label).__name__,
            type(self.size_label).__name__,
        )

        self.status_label.setText(f"{code}  {response.reason}")
        self.status_label.setStyleSheet(f"font-weight: bold; color: {color};")
        self.time_label.setText(f"{int(response.elapsed * 1000)} ms")
        self.size_label.setText(format_size(response.size))

        # Verify values were actually set
        logger.debug(
            "_update_status_bar: set status_label text=%r time_label text=%r size_label text=%r",
            self.status_label.text(),
            self.time_label.text(),
            self.size_label.text(),
        )

        logger.debug(
            "_update_status_bar: %d %s, %d ms, %s",
            code, response.reason,
            int(response.elapsed * 1000),
            format_size(response.size),
        )

    @staticmethod
    def _get_status_color(status_code: int) -> str:
        """Get color for HTTP status code."""
        if status_code < 300:
            return Colors.GREEN
        elif status_code < 400:
            return Colors.AMBER
        else:
            return Colors.RED

    # ------------------------------------------------------------------
    # Body Rendering
    # ------------------------------------------------------------------

    def _display_body(self, response: Response) -> None:
        """Display response body, handling size warnings."""
        logger.debug(
            "_display_body: size=%d, threshold=%d, body_text=%s visible=%s",
            response.size, self._LARGE_BODY_THRESHOLD,
            type(self.body_text).__name__,
            self.body_text.isVisible() if hasattr(self.body_text, 'isVisible') else 'N/A',
        )
        if response.size > self._LARGE_BODY_THRESHOLD:
            self._body_warning.setVisible(True)
            self._body_warn_label.setText(
                f"Response body is {format_size(response.size)} — rendering may be slow."
            )
            self.body_text.setPlaceholderText("Click 'Load Full' to display the body.")
            self.body_text.clear()
            logger.debug("_display_body: large body — deferred rendering")
        else:
            self._body_warning.setVisible(False)
            text = pretty_print_body(response)
            logger.debug("_display_body: calling body_text.set_code with %d chars", len(text))
            self.body_text.set_code(text)
            # Verify it was set
            actual_text = self.body_text.toPlainText() if hasattr(self.body_text, 'toPlainText') else '(N/A)'
            logger.debug("_display_body: set %d chars, body_text now contains %d chars", len(text), len(actual_text))

    # ------------------------------------------------------------------
    # JSON Tree
    # ------------------------------------------------------------------

    def _display_json_tree(self, response: Response) -> None:
        try:
            can_show_json = bool(response.is_json and response.size <= self._LARGE_BODY_THRESHOLD)
            logger.debug(
                "_display_json_tree: is_json=%s, can_show=%s",
                getattr(response, "is_json", None), can_show_json,
            )
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
            self.resp_headers_table.isVisible() if hasattr(self.resp_headers_table, 'isVisible') else 'N/A',
            type(self.tabs).__name__,
        )

        self.resp_headers_table.load(response.headers)
        count = len(response.headers)
        self._hdrs_count_label.setText(str(count))
        logger.debug("_display_headers: %d headers loaded, tabs visible=%s", count, self.tabs.isVisible())

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
        cookies = parse_cookies(headers)
        for cookie_name, attributes in cookies:
            self._add_cookie_row(cookie_name, attributes)
        self._update_cookies_tab_title()

    def _add_cookie_row(self, cookie_name: str, attributes: Dict[str, str]) -> None:
        """Add a single cookie row to the table."""
        row = self._cookies_table.rowCount()
        self._cookies_table.insertRow(row)

        self._cookies_table.setItem(row, 0, QTableWidgetItem(cookie_name))
        self._cookies_table.setItem(row, 1, QTableWidgetItem(attributes.get("value", "")))
        self._cookies_table.setItem(row, 2, QTableWidgetItem(attributes.get("domain", "")))
        self._cookies_table.setItem(row, 3, QTableWidgetItem(attributes.get("path", "")))
        self._cookies_table.setItem(row, 4, QTableWidgetItem(attributes.get("expires", "")))
        self._cookies_table.setItem(row, 5, QTableWidgetItem("✓" if attributes.get("secure") == "true" else ""))
        self._cookies_table.setItem(row, 6, QTableWidgetItem("✓" if attributes.get("httponly") == "true" else ""))

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
        self.sent_method_label.setStyleSheet(
            f"font-weight: bold; color: white; "
            f"background: {color}; padding: 2px 8px; border-radius: 3px;"
        )

    def _display_sent_request_url(self, response: Response) -> None:
        """Display the URL that was sent (prefer expanded httpx URL)."""
        req = response.request
        display_url = self._build_display_url(response)
        self.sent_url_label.setText(display_url)

    @staticmethod
    def _build_display_url(response: Response) -> str:
        """Build the display URL from sent_url or request URL with params."""
        if response.sent_url:
            return response.sent_url

        req = response.request
        params = getattr(req, "params", None)
        if params:
            return f"{req.url}?{urlencode(params)}"

        return req.url

    def _display_sent_request_headers(self, response: Response) -> None:
        """Display headers sent (prefer sent_headers which include auth)."""
        req = response.request
        sent_hdrs = response.sent_headers or req.headers or {}
        self.sent_headers_table.load(sent_hdrs)

    def _display_sent_request_body(self, body: Optional[Any]) -> None:
        """Display the request body, if present."""
        if body:
            self.sent_body_text.set_code(self._format_request_body(body))
        else:
            self.sent_body_text.setPlaceholderText("(no body)")
            self.sent_body_text.clear()

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
