"""
Response viewer panel — shows what was received AND what was sent.
Refactored for clarity, separation of concerns, and maintainability.
"""

from __future__ import annotations

import difflib
import http.cookies as _hc
import json
from typing import Optional, Dict, Any
from urllib.parse import urlencode

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTextEdit, QTabWidget,
    QTableWidget, QTableWidgetItem, QPushButton, QHeaderView, QApplication,
    QLineEdit, QToolButton, QMenu, QDialog, QComboBox, QPlainTextEdit,
    QListWidget, QListWidgetItem, QMessageBox, QFileDialog
)
from PyQt6.QtCore import Qt, QRunnable, QThreadPool, QObject, pyqtSignal
from PyQt6.QtGui import QKeySequence, QShortcut
import logging

from equinox.core.codegen import GENERATORS, generate_code
from equinox.core.request import Response
from equinox.gui.intelligence_panel import IntelligencePanel
from equinox.gui.response_panel.header_table import HeaderTable
from equinox.gui.response_panel.json_tree import JsonTree
from equinox.gui.response_panel.read_only_text import ReadOnlyText
from equinox.gui.response_panel.search_bar import SearchBar
from equinox.gui.syntax_highlighter import JsonHighlighter, XmlHighlighter, YamlHighlighter
from equinox.gui.theme import Colors, get_mono_font

logger = logging.getLogger(__name__)

# Maps content-type substrings (checked in order) to highlighter classes.
# First match wins, so more specific tokens should come first.
_CT_HIGHLIGHTERS = [
    ("json", JsonHighlighter),
    ("xml",  XmlHighlighter),
    ("html", XmlHighlighter),
    ("svg",  XmlHighlighter),
    ("yaml", YamlHighlighter),
    ("yml",  YamlHighlighter),
]


# ---------------------------------------------------------------------------
# Background pretty‑printer
# ---------------------------------------------------------------------------

class _WorkerSignals(QObject):
    result = pyqtSignal(object, str)  # (marker, formatted_text)


class _PrettyPrintRunnable(QRunnable):
    """
    Runs JSON/XML pretty‑printing off the UI thread.
    """

    def __init__(self, response: Response, marker: str):
        super().__init__()
        self.response = response
        self.marker = marker
        self.signals = _WorkerSignals()

    def run(self) -> None:
        try:
            text = self._format_body()
        except Exception:
            text = getattr(self.response, "text", "") or ""
        try:
            self.signals.result.emit(self.marker, text)
        except Exception:
            # If the UI is gone, just drop the result
            pass

    def _format_body(self) -> str:
        # JSON
        if getattr(self.response, "is_json", False):
            try:
                return json.dumps(self.response.json(), indent=2, ensure_ascii=False)
            except Exception:
                pass

        # XML / HTML
        ct = self.response.headers.get("content-type", "").lower()
        if any(x in ct for x in ("xml", "html", "svg")):
            try:
                import xml.dom.minidom
                return xml.dom.minidom.parseString(
                    self.response.text.encode()
                ).toprettyxml(indent="  ")
            except Exception:
                pass

        # Fallback
        return self.response.text


# ---------------------------------------------------------------------------
# Response Panel
# ---------------------------------------------------------------------------

class ResponsePanel(QWidget):
    """
    Panel for displaying HTTP responses and the request that was sent.
    """

    _LARGE_BODY_THRESHOLD = 2_097_152  # 2 MB

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_response: Optional[Response] = None
        self._thread_pool = QThreadPool.globalInstance()
        self._body_highlighter = None
        self._prefer_json_view = False
        self._init_ui()

    # ------------------------------------------------------------------
    # UI Construction
    # ------------------------------------------------------------------

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(4)

        self._build_status_bar(layout)
        self._build_timings_row(layout)
        self._build_tabs(layout)

    # ---------------- Status Bar ----------------

    def _build_status_bar(self, layout: QVBoxLayout) -> None:
        row = QHBoxLayout()

        self.status_label = QLabel("No response yet")
        self.status_label.setStyleSheet(f"font-weight: bold; color: {Colors.FG_MUTED};")

        self.time_label = QLabel("")
        self.time_label.setObjectName("mutedLabel")

        self.size_label = QLabel("")
        self.size_label.setObjectName("mutedLabel")

        copy_btn = QPushButton("Copy Body")
        copy_btn.setFixedWidth(80)
        copy_btn.setToolTip("Copy response body to clipboard")
        copy_btn.clicked.connect(self._copy_body)

        download_btn = QPushButton("Download…")
        download_btn.setFixedWidth(90)
        download_btn.setToolTip("Save response body to a file")
        download_btn.clicked.connect(self._download_body)

        code_btn = self._build_code_button()

        self._wrap_btn = QToolButton()
        self._wrap_btn.setText("Wrap")
        self._wrap_btn.setCheckable(True)
        self._wrap_btn.setChecked(False)
        self._wrap_btn.setToolTip("Toggle line wrapping in response body")
        self._wrap_btn.toggled.connect(self._toggle_word_wrap)

        self._view_btn, self._view_menu = self._build_view_selector()

        diff_btn = QPushButton("Diff…")
        diff_btn.setFixedWidth(56)
        diff_btn.setToolTip("Compare response body with a history entry")
        diff_btn.clicked.connect(self._diff_with_history)

        row.addWidget(self.status_label)
        row.addStretch()
        row.addWidget(self.time_label)
        row.addWidget(QLabel("|"))
        row.addWidget(self.size_label)
        row.addWidget(self._wrap_btn)
        row.addWidget(self._view_btn)
        row.addWidget(diff_btn)
        row.addWidget(copy_btn)
        row.addWidget(download_btn)
        row.addWidget(code_btn)

        layout.addLayout(row)

    def _build_code_button(self) -> QToolButton:
        btn = QToolButton()
        btn.setText("Code…")
        btn.setToolTip("Generate client code for this request")
        btn.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)

        menu = QMenu(btn)
        for fmt in GENERATORS:
            act = menu.addAction(fmt)
            act.triggered.connect(lambda _, f=fmt: self._copy_as_code(f))

        menu.addSeparator()
        view_act = menu.addAction("View…")
        view_act.triggered.connect(self._view_code_dialog)

        btn.setMenu(menu)
        btn.clicked.connect(self._view_code_dialog)
        return btn

    def _build_view_selector(self):
        btn = QToolButton()
        btn.setText("View")
        btn.setToolTip("Switch between Raw and JSON Tree view")
        btn.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)

        menu = QMenu(btn)
        raw_act = menu.addAction("Raw")
        json_act = menu.addAction("JSON Tree")
        raw_act.setCheckable(True)
        json_act.setCheckable(True)
        raw_act.setChecked(True)
        json_act.setChecked(False)

        raw_act.triggered.connect(lambda: self._on_view_selected("raw"))
        json_act.triggered.connect(lambda: self._on_view_selected("json"))
        btn.setMenu(menu)
        btn.clicked.connect(btn.showMenu)

        self._view_raw_act = raw_act
        self._view_json_act = json_act

        return btn, menu

    # ---------------- Timings Row ----------------

    def _build_timings_row(self, layout: QVBoxLayout) -> None:
        row = QHBoxLayout()

        self._timings_toggle = QToolButton()
        self._timings_toggle.setText("▶ Timings")
        self._timings_toggle.setCheckable(True)
        self._timings_toggle.setVisible(False)
        self._timings_toggle.clicked.connect(self._on_timings_toggled)

        self._timings_label = QLabel()
        self._timings_label.setObjectName("mutedLabel")
        self._timings_label.setVisible(False)

        row.addWidget(self._timings_toggle)
        row.addWidget(self._timings_label)
        row.addStretch()

        layout.addLayout(row)

    # ---------------- Tabs ----------------

    def _build_tabs(self, layout: QVBoxLayout) -> None:
        self.tabs = QTabWidget()

        self._build_body_tab()
        self._build_headers_tab()
        self._build_cookies_tab()
        self._build_json_tab()
        self._build_sent_request_tab()
        self._build_intelligence_tab()

        layout.addWidget(self.tabs, 1)

    # ------------------------------------------------------------------
    # Body Tab
    # ------------------------------------------------------------------

    def _build_body_tab(self) -> None:
        container = QWidget()
        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(0)

        # Large-body warning
        self._body_warning = QWidget()
        warn_row = QHBoxLayout(self._body_warning)
        warn_row.setContentsMargins(4, 2, 4, 2)

        self._body_warn_label = QLabel()
        self._body_warn_label.setStyleSheet(f"color: {Colors.AMBER}; font-weight: bold;")

        load_btn = QPushButton("Load Full")
        load_btn.setFixedWidth(100)
        load_btn.clicked.connect(self._load_large_body)

        warn_row.addWidget(self._body_warn_label)
        warn_row.addStretch()
        warn_row.addWidget(load_btn)
        self._body_warning.setVisible(False)
        vbox.addWidget(self._body_warning)

        # Loading indicator
        self._loading_label = QLabel("Loading…")
        self._loading_label.setObjectName("mutedLabel")
        self._loading_label.setVisible(False)
        vbox.addWidget(self._loading_label)

        # Body text
        self.body_text = ReadOnlyText()

        # Search bar
        self._search_bar = SearchBar(self.body_text, container)
        self._search_bar.set_filter_callback(self._on_jsonpath_filter)

        vbox.addWidget(self.body_text, 1)
        vbox.addWidget(self._search_bar)

        self._body_tab_idx = self.tabs.addTab(container, "Body")

        # Ctrl+F shortcut
        QShortcut(QKeySequence("Ctrl+F"), self).activated.connect(self._open_search)

        # Escape hides the search bar when it or its children have focus
        esc = QShortcut(QKeySequence("Escape"), self._search_bar)
        esc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        esc.activated.connect(self._search_bar.hide)

    # ------------------------------------------------------------------
    # Headers Tab
    # ------------------------------------------------------------------

    def _build_headers_tab(self) -> None:
        container = QWidget()
        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(0, 2, 0, 0)
        vbox.setSpacing(2)

        search_row = QHBoxLayout()
        self._hdrs_search = QLineEdit()
        self._hdrs_search.setPlaceholderText("Filter headers…")
        self._hdrs_search.setFixedHeight(24)
        self._hdrs_search.setClearButtonEnabled(True)
        self._hdrs_search.textChanged.connect(self._on_hdrs_filter_changed)

        self._hdrs_count_label = QLabel("")
        self._hdrs_count_label.setObjectName("mutedLabel")

        search_row.addWidget(self._hdrs_search, 1)
        search_row.addWidget(self._hdrs_count_label)
        vbox.addLayout(search_row)

        self.resp_headers_table = HeaderTable()
        vbox.addWidget(self.resp_headers_table, 1)

        self.tabs.addTab(container, "Headers")

    # ------------------------------------------------------------------
    # Cookies Tab
    # ------------------------------------------------------------------

    def _build_cookies_tab(self) -> None:
        container = QWidget()
        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(0, 2, 0, 0)

        self._cookies_table = QTableWidget(0, 7)
        self._cookies_table.setHorizontalHeaderLabels(
            ["Name", "Value", "Domain", "Path", "Expires", "Secure", "HttpOnly"]
        )
        hdr = self._cookies_table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for col in (2, 3, 4, 5, 6):
            hdr.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)

        self._cookies_table.verticalHeader().setVisible(False)
        self._cookies_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._cookies_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._cookies_table.setAlternatingRowColors(True)

        vbox.addWidget(self._cookies_table, 1)
        self.tabs.addTab(container, "Cookies")

    # ------------------------------------------------------------------
    # JSON Tree Tab
    # ------------------------------------------------------------------

    def _build_json_tab(self) -> None:
        self._json_tree = JsonTree()
        self._json_tab_idx = self.tabs.addTab(self._json_tree, "JSON")

    # ------------------------------------------------------------------
    # Sent Request Tab
    # ------------------------------------------------------------------

    def _build_sent_request_tab(self) -> None:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        # Request line
        row = QHBoxLayout()
        self.sent_method_label = QLabel("—")
        self.sent_method_label.setStyleSheet(
            f"font-weight: bold; "
            f"background: {Colors.BG_ALT}; padding: 2px 8px; border-radius: 3px;"
        )

        self.sent_url_label = QLabel("—")
        self.sent_url_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.sent_url_label.setWordWrap(True)
        self.sent_url_label.setFont(get_mono_font())

        copy_curl_btn = QPushButton("Copy as cURL")
        copy_curl_btn.setFixedWidth(110)
        copy_curl_btn.setToolTip("Copy the request as a cURL command")
        copy_curl_btn.clicked.connect(self._copy_as_curl)

        row.addWidget(self.sent_method_label)
        row.addWidget(self.sent_url_label, 1)
        row.addWidget(copy_curl_btn)
        layout.addLayout(row)

        # Headers
        layout.addWidget(QLabel("Request Headers (as sent — includes auth):"))
        self.sent_headers_table = HeaderTable()
        layout.addWidget(self.sent_headers_table, 2)

        # Body
        layout.addWidget(QLabel("Request Body:"))
        self.sent_body_text = ReadOnlyText()
        self.sent_body_text.setMaximumHeight(180)
        layout.addWidget(self.sent_body_text, 1)

        self.tabs.addTab(container, "Sent Request")

    # ------------------------------------------------------------------
    # Intelligence Tab
    # ------------------------------------------------------------------

    def _build_intelligence_tab(self) -> None:
        self.intelligence_panel = IntelligencePanel()
        self.tabs.addTab(self.intelligence_panel, "Intelligence")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def display_response(self, response: Response) -> None:
        """
        Main entry point: display a new HTTP response.
        """
        try:
            self.current_response = response

            logger.debug("ResponsePanel.display_response: starting for response (status=%s size=%s)", getattr(response, 'status_code', None), getattr(response, 'size', None))

            self._update_status_bar(response)

            logger.debug("ResponsePanel.display_response: applying highlighter")
            try:
                self._apply_highlighter(response.headers.get("content-type", ""))
            except Exception:
                # Highlighter creation should never crash the UI — log and continue
                logger.exception("_apply_highlighter raised an exception for content-type=%s", response.headers.get("content-type", ""))

            # Body / JSON tree / headers etc. — each has internal guards but protect the whole flow
            logger.debug("ResponsePanel.display_response: about to call _display_body")
            try:
                self._display_body(response)
            except Exception:
                logger.exception("_display_body failed for response; falling back to raw text")
                try:
                    # Fallback: clear and show raw text
                    self.body_text.clear()
                    self.body_text.set_code(getattr(response, "text", ""))
                except Exception:
                    logger.exception("Fallback body display also failed")

            logger.debug("ResponsePanel.display_response: about to call _display_json_tree")
            try:
                self._display_json_tree(response)
            except Exception:
                logger.exception("_display_json_tree failed")

            logger.debug("ResponsePanel.display_response: about to call _display_headers")
            try:
                self._display_headers(response)
            except Exception:
                logger.exception("_display_headers failed")

            logger.debug("ResponsePanel.display_response: about to call _display_timings")
            try:
                self._display_timings(response)
            except Exception:
                logger.exception("_display_timings failed")

            logger.debug("ResponsePanel.display_response: about to call _load_cookies_tab")
            try:
                self._load_cookies_tab(response.headers)
            except Exception:
                logger.exception("_load_cookies_tab failed")

            logger.debug("ResponsePanel.display_response: about to call _display_sent_request")
            try:
                self._display_sent_request(response)
            except Exception:
                logger.exception("_display_sent_request failed")

            # Switch view
            logger.debug("ResponsePanel.display_response: switching view (prefer_json=%s view_json_enabled=%s)", self._prefer_json_view, getattr(self, '_view_json_act', None) and self._view_json_act.isEnabled())
            if self._prefer_json_view and self._view_json_act.isEnabled():
                self._switch_to_json_view()
            else:
                self._switch_to_raw_view()
        except Exception:
            # Catch-all: ensure we log the traceback and surface a non-fatal error
            logger.exception("Unhandled exception in ResponsePanel.display_response")
            try:
                QMessageBox.critical(
                    self,
                    "Display Error",
                    "An unexpected error occurred while displaying the response. See logs for details.",
                )
            except Exception:
                logger.debug("Also failed to show error dialog after display_response exception", exc_info=True)

    def set_intelligence_badge(self, count: int) -> None:
        """Set a badge showing the number of intelligence findings."""
        idx = self.tabs.indexOf(self.intelligence_panel)
        if idx < 0:
            return
        if count > 0:
            self.tabs.setTabText(idx, f"Intelligence ({count})")
        else:
            self.tabs.setTabText(idx, "Intelligence")

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
        """Pretty‑print JSON or XML if possible."""
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
    # Large body loading
    # ------------------------------------------------------------------

    def _load_large_body(self) -> None:
        """User confirmed loading a large body."""
        if self.current_response is None:
            return

        self._body_warning.setVisible(False)
        self._loading_label.setVisible(True)

        marker = getattr(self.current_response, "sent_url", None) or getattr(
            self.current_response.request, "url", None
        )
        runnable = _PrettyPrintRunnable(self.current_response, marker)
        runnable.signals.result.connect(self._on_pretty_result)
        self._thread_pool.start(runnable)

    def _on_pretty_result(self, marker: object, formatted_text: str) -> None:
        """Handle formatted body results from background worker."""
        self._loading_label.setVisible(False)
        try:
            cur_marker = getattr(self.current_response, "sent_url", None) or getattr(
                self.current_response.request, "url", None
            )
            if cur_marker != marker:
                return
            self.body_text.set_code(formatted_text)
        except Exception:
            logger.exception("Unexpected error in _on_pretty_result; falling back to raw body")
            if self.current_response is not None:
                try:
                    self.body_text.set_code(self._pretty_body(self.current_response))
                except Exception:
                    logger.exception("Fallback body display also failed")
            else:
                self.body_text.clear()

    # ------------------------------------------------------------------
    # JSONPath filter callback
    # ------------------------------------------------------------------

    def _on_jsonpath_filter(self, filtered_text: Optional[str]) -> None:
        """Receive filtered JSON text from the SearchBar and update the body view."""
        try:
            if filtered_text is None:
                if self.current_response is None:
                    self.body_text.clear()
                else:
                    self.body_text.set_code(self._pretty_body(self.current_response))
            else:
                self.body_text.set_code(filtered_text)
        except Exception:
            # If anything goes wrong, fall back to original body
            if self.current_response is not None:
                self.body_text.set_code(self._pretty_body(self.current_response))
            else:
                self.body_text.clear()

    # ------------------------------------------------------------------
    # Diff with history
    # ------------------------------------------------------------------

    def _diff_with_history(self) -> None:
        """Open a dialog to compare the current response body with a history entry."""
        if self.current_response is None:
            return

        history_entries = self._fetch_history_entries()
        if not history_entries:
            QMessageBox.information(
                self,
                "Diff vs. History",
                "No matching history entries found for this request.",
            )
            return

        entry = self._pick_history_entry(history_entries)
        if entry is None:
            return

        old_body = entry.get("response_body") or ""
        displayed = self.body_text.toPlainText()
        new_body = displayed if displayed else self._pretty_body(self.current_response)
        self._show_diff_dialog(old_body, new_body)

    def _fetch_history_entries(self) -> list:
        """Return recent history entries matching the current request, or []."""
        db = None
        try:
            db = self.window().db
        except Exception:
            pass
        if db is None:
            return []
        try:
            from equinox.storage import HistoryManager
            req = self.current_response.request
            return HistoryManager(db).search_history(
                query=req.url, method=req.method, limit=30
            )
        except Exception:
            logger.exception("Failed to fetch history entries for diff")
            return []

    def _pick_history_entry(self, history_entries: list) -> "Optional[Dict[str, Any]]":
        """Show a picker dialog and return the selected history entry, or None."""
        picker = QDialog(self)
        picker.setWindowTitle("Choose History Entry")
        picker.setMinimumSize(480, 280)
        pk_layout = QVBoxLayout(picker)
        pk_layout.addWidget(QLabel("Select a history entry to compare against:"))

        list_widget = QListWidget()
        for entry in history_entries:
            ts = entry.get("executed_at", "")[:19]
            sc = entry.get("status_code", "?")
            label = f"{ts}  {entry.get('method', '')}  {entry.get('url', '')}  [{sc}]"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, entry)
            list_widget.addItem(item)
        pk_layout.addWidget(list_widget, 1)

        btn_row = QHBoxLayout()
        cancel_btn = QPushButton("Cancel")
        compare_btn = QPushButton("Compare")
        compare_btn.setEnabled(False)
        btn_row.addStretch()
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(compare_btn)
        pk_layout.addLayout(btn_row)

        cancel_btn.clicked.connect(picker.reject)
        compare_btn.clicked.connect(picker.accept)
        list_widget.currentItemChanged.connect(
            lambda cur, _: compare_btn.setEnabled(cur is not None)
        )

        if picker.exec() != QDialog.DialogCode.Accepted:
            return None
        selected = list_widget.currentItem()
        return selected.data(Qt.ItemDataRole.UserRole) if selected else None

    def _show_diff_dialog(self, old_body: str, new_body: str) -> None:
        """Display a unified diff between *old_body* and *new_body*."""
        old_lines = old_body.splitlines(keepends=True)
        new_lines = new_body.splitlines(keepends=True)
        diff_lines = list(
            difflib.unified_diff(
                old_lines, new_lines, fromfile="History", tofile="Current", lineterm=""
            )
        )
        diff_text = "".join(diff_lines) if diff_lines else "(No differences)"

        dlg = QDialog(self)
        dlg.setWindowTitle("Response Body Diff")
        dlg.setMinimumSize(700, 500)
        dv_layout = QVBoxLayout(dlg)
        diff_editor = QPlainTextEdit()
        diff_editor.setReadOnly(True)
        diff_editor.setFont(get_mono_font())
        diff_editor.setPlainText(diff_text)
        dv_layout.addWidget(diff_editor, 1)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dlg.accept)
        dv_layout.addWidget(close_btn)
        dlg.exec()

    # ------------------------------------------------------------------
    # View switching
    # ------------------------------------------------------------------

    def _on_view_selected(self, mode: str) -> None:
        if mode == "json":
            self._prefer_json_view = True
            self._switch_to_json_view()
        else:
            self._prefer_json_view = False
            self._switch_to_raw_view()

    def _switch_to_raw_view(self) -> None:
        self._view_raw_act.setChecked(True)
        self._view_json_act.setChecked(False)
        self.tabs.setCurrentIndex(self._body_tab_idx)

    def _switch_to_json_view(self) -> None:
        if not self._view_json_act.isEnabled():
            self._switch_to_raw_view()
            return
        self._view_raw_act.setChecked(False)
        self._view_json_act.setChecked(True)
        self.tabs.setCurrentIndex(self._json_tab_idx)

    # ------------------------------------------------------------------
    # Highlighter
    # ------------------------------------------------------------------

    def _apply_highlighter(self, content_type: str) -> None:
        """Apply syntax highlighter based on content-type."""
        if self._body_highlighter is not None:
            self._body_highlighter.setDocument(None)
            self._body_highlighter = None

        ct = (content_type or "").lower()
        cls = next((h for token, h in _CT_HIGHLIGHTERS if token in ct), None)
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
    # Copy / Download / Codegen
    # ------------------------------------------------------------------

    def _copy_body(self) -> None:
        """Copy the current body text to the clipboard."""
        text = self.body_text.toPlainText()
        if not text:
            return
        QApplication.clipboard().setText(text)

    def _download_body(self) -> None:
        """Save the current body text to a file."""
        if self.current_response is None:
            return

        suggested = "response.txt"
        ct = self.current_response.headers.get("content-type", "").lower()
        if "json" in ct:
            suggested = "response.json"
        elif "xml" in ct:
            suggested = "response.xml"
        elif "html" in ct:
            suggested = "response.html"

        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Response Body",
            suggested,
            "All Files (*.*)",
        )
        if not path:
            return

        text = self.body_text.toPlainText() or self._pretty_body(self.current_response)
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
        except Exception as exc:
            QMessageBox.warning(self, "Save Failed", f"Could not save file:\n{exc}")

    def _copy_as_code(self, fmt: str) -> None:
        """Generate client code for this request and copy to clipboard."""
        if self.current_response is None:
            return
        try:
            code = generate_code(fmt, self.current_response.request)
        except Exception as exc:
            QMessageBox.warning(self, "Code Generation Failed", str(exc))
            return
        QApplication.clipboard().setText(code)

    def _view_code_dialog(self) -> None:
        """Show a dialog with generated client code."""
        if self.current_response is None:
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("Generate Client Code")
        dlg.setMinimumSize(640, 480)
        layout = QVBoxLayout(dlg)

        row = QHBoxLayout()
        row.addWidget(QLabel("Language / Format:"))
        combo = QComboBox()
        combo.addItems(list(GENERATORS.keys()))
        row.addWidget(combo, 1)
        layout.addLayout(row)

        editor = QPlainTextEdit()
        editor.setReadOnly(True)
        editor.setFont(get_mono_font())
        layout.addWidget(editor, 1)

        btn_row = QHBoxLayout()
        copy_btn = QPushButton("Copy")
        close_btn = QPushButton("Close")
        btn_row.addStretch()
        btn_row.addWidget(copy_btn)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

        def update_code() -> None:
            fmt = combo.currentText()
            try:
                code = generate_code(fmt, self.current_response.request)
            except Exception as exc:
                code = f"# Error generating code: {exc}"
            editor.setPlainText(code)

        combo.currentIndexChanged.connect(update_code)
        update_code()

        copy_btn.clicked.connect(lambda: QApplication.clipboard().setText(editor.toPlainText()))
        close_btn.clicked.connect(dlg.accept)

        dlg.exec()

    def _copy_as_curl(self) -> None:
        """Copy the request as a cURL command."""
        self._copy_as_code("cURL")

    # ------------------------------------------------------------------
    # Misc helpers
    # ------------------------------------------------------------------

    def _toggle_word_wrap(self, checked: bool) -> None:
        """Toggle line wrapping in the response body text view."""
        mode = QTextEdit.LineWrapMode.WidgetWidth if checked else QTextEdit.LineWrapMode.NoWrap
        self.body_text.setLineWrapMode(mode)

    def _open_search(self) -> None:
        """Open the inline search bar when Ctrl+F is pressed."""
        if self.tabs.currentIndex() != self._body_tab_idx:
            self.tabs.setCurrentIndex(self._body_tab_idx)
        self._search_bar.show_and_focus()

    @staticmethod
    def _format_size(size: int) -> str:
        """Human‑readable size."""
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024 or unit == "GB":
                return f"{size:.1f} {unit}" if unit != "B" else f"{size} {unit}"
            size /= 1024.0
        return f"{size:.1f} GB"