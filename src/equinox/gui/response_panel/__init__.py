"""Response viewer panel — shows what was received AND what was sent."""

import difflib
import http.cookies as _hc
import json
import os
import shlex
from typing import Optional
from urllib.parse import urlencode

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTextEdit,
    QTabWidget, QTableWidget, QTableWidgetItem, QPushButton,
    QHeaderView, QApplication, QLineEdit, QToolButton,
    QMenu, QDialog, QComboBox, QPlainTextEdit, QListWidget, QListWidgetItem,
    QMessageBox, QFileDialog,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeySequence, QShortcut

from equinox.core.codegen import GENERATORS, generate_code
from equinox.core.request import Response
from equinox.gui.intelligence_panel import IntelligencePanel
from equinox.gui.response_panel.header_table import HeaderTable
from equinox.gui.response_panel.json_tree import JsonTree
from equinox.gui.response_panel.read_only_text import ReadOnlyText
from equinox.gui.response_panel.search_bar import SearchBar
from equinox.gui.syntax_highlighter import JsonHighlighter, XmlHighlighter, YamlHighlighter
from equinox.gui.theme import Colors, get_mono_font


class ResponsePanel(QWidget):
    """Panel for displaying HTTP responses and the request that was sent."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_response: Optional[Response] = None
        self._init_ui()

    # ── UI construction ───────────────────────────────────────────────

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(4)

        # ── Status bar ────────────────────────────────────────────────
        status_row = QHBoxLayout()

        self.status_label = QLabel("No response yet")
        self.status_label.setStyleSheet(f"font-weight: bold; color: {Colors.FG_MUTED};")

        self.time_label = QLabel("")
        self.time_label.setObjectName("mutedLabel")
        self.size_label = QLabel("")
        self.size_label.setObjectName("mutedLabel")

        copy_btn = QPushButton("Copy Body")
        copy_btn.setFixedWidth(80)
        copy_btn.clicked.connect(self._copy_body)
        copy_btn.setToolTip("Copy response body to clipboard")

        download_btn = QPushButton("Download…")
        download_btn.setFixedWidth(90)
        download_btn.clicked.connect(self._download_body)
        download_btn.setToolTip("Save response body to a file")

        # Code generation button with drop-down menu
        code_btn = QToolButton()
        code_btn.setText("Code…")
        code_btn.setToolTip("Generate client code for this request")
        code_btn.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        code_menu = QMenu()
        for fmt in list(GENERATORS.keys()) + ["cURL"]:
            act = code_menu.addAction(fmt)
            act.triggered.connect(lambda checked, f=fmt: self._copy_as_code(f))
        code_menu.addSeparator()
        view_act = code_menu.addAction("View…")
        view_act.triggered.connect(self._view_code_dialog)
        code_btn.setMenu(code_menu)
        code_btn.clicked.connect(self._view_code_dialog)

        self._wrap_btn = QToolButton()
        self._wrap_btn.setText("Wrap")
        self._wrap_btn.setCheckable(True)
        self._wrap_btn.setChecked(False)
        self._wrap_btn.setToolTip("Toggle line wrapping in response body")
        self._wrap_btn.clicked.connect(self._toggle_word_wrap)

        # View selector: Raw / JSON Tree
        self._view_btn = QToolButton()
        self._view_btn.setText("View")
        self._view_btn.setToolTip("Switch between Raw and JSON Tree view")
        self._view_btn.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self._view_menu = QMenu(self._view_btn)
        self._view_raw_act = self._view_menu.addAction("Raw")
        self._view_json_act = self._view_menu.addAction("JSON Tree")
        self._view_raw_act.setCheckable(True)
        self._view_json_act.setCheckable(True)
        self._view_raw_act.setChecked(True)
        self._view_json_act.setChecked(False)
        self._view_btn.setMenu(self._view_menu)
        self._prefer_json_view = False
        self._view_raw_act.triggered.connect(lambda: self._on_view_selected("raw"))
        self._view_json_act.triggered.connect(lambda: self._on_view_selected("json"))
        # Clicking the main button area opens the menu (same as the arrow)
        self._view_btn.clicked.connect(self._view_btn.showMenu)

        diff_btn = QPushButton("Diff…")
        diff_btn.setFixedWidth(56)
        diff_btn.setToolTip("Compare response body with a history entry")
        diff_btn.clicked.connect(self._diff_with_history)

        status_row.addWidget(self.status_label)
        status_row.addStretch()
        status_row.addWidget(self.time_label)
        status_row.addWidget(QLabel("|"))
        status_row.addWidget(self.size_label)
        status_row.addWidget(self._wrap_btn)
        status_row.addWidget(self._view_btn)
        status_row.addWidget(diff_btn)
        status_row.addWidget(copy_btn)
        status_row.addWidget(download_btn)
        status_row.addWidget(code_btn)
        layout.addLayout(status_row)

        # Collapsible timings row (hidden until a response with timings is received)
        timings_row = QHBoxLayout()
        self._timings_toggle = QToolButton()
        self._timings_toggle.setText("▶ Timings")
        self._timings_toggle.setCheckable(True)
        self._timings_toggle.setVisible(False)
        self._timings_toggle.clicked.connect(self._on_timings_toggled)
        self._timings_label = QLabel()
        self._timings_label.setObjectName("mutedLabel")
        self._timings_label.setVisible(False)
        timings_row.addWidget(self._timings_toggle)
        timings_row.addWidget(self._timings_label)
        timings_row.addStretch()
        layout.addLayout(timings_row)

        # ── Tabs ──────────────────────────────────────────────────────
        self.tabs = QTabWidget()

        # Response Body — wrap in a widget to include search bar + size warning (#10)
        body_container = QWidget()
        body_vbox = QVBoxLayout(body_container)
        body_vbox.setContentsMargins(0, 0, 0, 0)
        body_vbox.setSpacing(0)

        # Large-body warning bar (hidden by default)
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
        body_vbox.addWidget(self._body_warning)

        self.body_text = ReadOnlyText()
        self._body_highlighter = None  # set dynamically per content-type
        self._search_bar = SearchBar(self.body_text, body_container)
        # Register a callback so JSONPath mode can ask us to display a filtered
        # representation of the JSON body (only matched values are shown).
        self._search_bar.set_filter_callback(self._on_jsonpath_filter)
        body_vbox.addWidget(self.body_text, 1)
        body_vbox.addWidget(self._search_bar)
        self._body_tab_idx = self.tabs.addTab(body_container, "Body")

        # Ctrl+F to open search (works when body tab is visible)
        find_shortcut = QShortcut(QKeySequence("Ctrl+F"), self)
        find_shortcut.activated.connect(self._open_search)

        # Response Headers — with search filter (#8)
        hdrs_container = QWidget()
        hdrs_vbox = QVBoxLayout(hdrs_container)
        hdrs_vbox.setContentsMargins(0, 2, 0, 0)
        hdrs_vbox.setSpacing(2)
        hdrs_search_row = QHBoxLayout()
        self._hdrs_search = QLineEdit()
        self._hdrs_search.setPlaceholderText("Filter headers…")
        self._hdrs_search.setFixedHeight(24)
        self._hdrs_search.setClearButtonEnabled(True)
        self._hdrs_search.textChanged.connect(self._on_hdrs_filter_changed)
        self._hdrs_count_label = QLabel("")
        self._hdrs_count_label.setObjectName("mutedLabel")
        hdrs_search_row.addWidget(self._hdrs_search, 1)
        hdrs_search_row.addWidget(self._hdrs_count_label)
        hdrs_vbox.addLayout(hdrs_search_row)
        self.resp_headers_table = HeaderTable()
        hdrs_vbox.addWidget(self.resp_headers_table, 1)
        self.tabs.addTab(hdrs_container, "Headers")

        # ── Cookies tab ───────────────────────────────────────────────
        cookies_widget = QWidget()
        cookies_vbox = QVBoxLayout(cookies_widget)
        cookies_vbox.setContentsMargins(0, 2, 0, 0)
        self._cookies_table = QTableWidget(0, 7)
        self._cookies_table.setHorizontalHeaderLabels(
            ["Name", "Value", "Domain", "Path", "Expires", "Secure", "HttpOnly"]
        )
        _ck_hdr = self._cookies_table.horizontalHeader()
        _ck_hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        _ck_hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for col in (2, 3, 4, 5, 6):
            _ck_hdr.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self._cookies_table.verticalHeader().setVisible(False)
        self._cookies_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._cookies_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._cookies_table.setAlternatingRowColors(True)
        cookies_vbox.addWidget(self._cookies_table, 1)
        self.tabs.addTab(cookies_widget, "Cookies")

        # JSON Tree tab (collapsible view for JSON objects)
        self._json_tree = JsonTree()
        self._json_tab_idx = self.tabs.addTab(self._json_tree, "JSON")

        # ── Sent Request tab ──────────────────────────────────────────
        sent_widget = self._build_sent_request_tab()
        self.tabs.addTab(sent_widget, "Sent Request")

        # ── Intelligence tab ──────────────────────────────────────────
        self.intelligence_panel = IntelligencePanel()
        self.tabs.addTab(self.intelligence_panel, "Intelligence")

        layout.addWidget(self.tabs, 1)

    def _build_sent_request_tab(self) -> QWidget:
        """Build the 'Sent Request' tab — shows what was actually transmitted."""
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        # ── Request line (METHOD  URL) ─────────────────────────────
        req_line_row = QHBoxLayout()
        self.sent_method_label = QLabel("—")
        self.sent_method_label.setStyleSheet(
            f"font-weight: bold; "
            f"background: {Colors.BG_ALT}; padding: 2px 8px; border-radius: 3px;"
        )
        self.sent_url_label = QLabel("—")
        self.sent_url_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.sent_url_label.setWordWrap(True)
        self.sent_url_label.setFont(get_mono_font())

        copy_curl_btn = QPushButton("Copy as cURL")
        copy_curl_btn.setFixedWidth(110)
        copy_curl_btn.clicked.connect(self._copy_as_curl)
        copy_curl_btn.setToolTip("Copy the request as a cURL command")

        req_line_row.addWidget(self.sent_method_label)
        req_line_row.addWidget(self.sent_url_label, 1)
        req_line_row.addWidget(copy_curl_btn)
        layout.addLayout(req_line_row)

        # ── Sent Headers ──────────────────────────────────────────────
        layout.addWidget(QLabel("Request Headers (as sent — includes auth):"))
        self.sent_headers_table = HeaderTable()
        layout.addWidget(self.sent_headers_table, 2)

        # ── Request Body ──────────────────────────────────────────────
        layout.addWidget(QLabel("Request Body:"))
        self.sent_body_text = ReadOnlyText()
        self.sent_body_text.setMaximumHeight(180)
        layout.addWidget(self.sent_body_text, 1)

        return w

    # ── Public API ────────────────────────────────────────────────────

    _LARGE_BODY_THRESHOLD = 2_097_152  # 2 MB

    def display_response(self, response: Response) -> None:
        self.current_response = response

        # Status
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

        # Apply syntax highlighter based on content-type
        self._apply_highlighter(response.headers.get("content-type", ""))

        # Body — show warning for large responses (#10)
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

        # JSON tree: populate when content is JSON and body is loaded
        try:
            can_show_json = bool(response.is_json and response.size <= self._LARGE_BODY_THRESHOLD)
            if can_show_json:
                obj = response.json()
                self._json_tree.load_json(obj)
                self._search_bar.set_json_doc(obj)  # enable JSONPath search
            else:
                self._json_tree.clear()
                self._search_bar.set_json_doc(None)  # no JSON available
            # Show/hide the JSON tab and enable/disable its View menu entry
            self.tabs.setTabVisible(self._json_tab_idx, can_show_json)
            self._view_json_act.setEnabled(can_show_json)
        except Exception:
            self._json_tree.clear()
            self._search_bar.set_json_doc(None)
            self.tabs.setTabVisible(self._json_tab_idx, False)
            self._view_json_act.setEnabled(False)
            can_show_json = False

        # Response headers (reset filter on new response)
        self._hdrs_search.blockSignals(True)
        self._hdrs_search.clear()
        self._hdrs_search.blockSignals(False)
        self.resp_headers_table.load(response.headers)
        self._hdrs_count_label.setText(f"{len(response.headers)}")

        # Timings row — reset collapse state first so each response starts fresh
        self._timings_toggle.setChecked(False)
        self._timings_toggle.setText("▶ Timings")
        self._timings_label.setVisible(False)
        timings = getattr(response, "timings", None)
        if timings:
            total = timings.get("total_ms", int(response.elapsed * 1000))
            parts = [f"Total: {total} ms"]
            for key in ("dns_ms", "connect_ms", "tls_ms", "ttfb_ms", "transfer_ms"):
                if key in timings:
                    label = key.replace("_ms", "").replace("ttfb", "TTFB").upper()
                    parts.append(f"{label}: {timings[key]} ms")
            self._timings_label.setText("  ·  ".join(parts))
            self._timings_toggle.setVisible(True)
        else:
            self._timings_toggle.setVisible(False)
            self._timings_label.setVisible(False)

        # Cookies tab — parse Set-Cookie headers
        self._load_cookies_tab(response.headers)

        # Sent request tab
        self._display_sent_request(response)

        # Switch to Body or JSON tab based on user preference
        if self._prefer_json_view and self._view_json_act.isEnabled():
            self._switch_to_json_view()
        else:
            self._switch_to_raw_view()

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
        if not response.sent_url and req.params:
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

    def set_intelligence_badge(self, count: int) -> None:
        """Set a badge showing the number of intelligence findings.
        
        Args:
            count: Number of findings (0 means no issues)
        """
        # Find the Intelligence tab index
        intelligence_tab_idx = self.tabs.indexOf(self.intelligence_panel)
        if intelligence_tab_idx < 0:
            return
        
        # Format the tab label with a badge if there are findings
        if count > 0:
            badge_text = f"Intelligence ({count})"
            self.tabs.setTabText(intelligence_tab_idx, badge_text)
        else:
            self.tabs.setTabText(intelligence_tab_idx, "Intelligence")

    # ── Helpers ───────────────────────────────────────────────────────

    def _load_cookies_tab(self, headers: dict) -> None:
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

    def _diff_with_history(self) -> None:
        """Open a dialog to compare the current response body with a history entry."""
        if self.current_response is None:
            return


        # Try to get a DB reference through the parent window
        db = None
        try:
            db = self.window().db
        except Exception:
            pass

        history_entries = []
        if db is not None:
            try:
                from equinox.storage import HistoryManager
                mgr = HistoryManager(db)
                req = self.current_response.request
                entries = mgr.search_history(
                    query=req.url, method=req.method, limit=30
                )
                history_entries = entries
            except Exception:
                pass

        if not history_entries:
            QMessageBox.information(
                self, "Diff vs. History",
                "No matching history entries found for this request."
            )
            return

        # Picker dialog
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
        btn_box = QPushButton("Compare")
        btn_box.setEnabled(False)  # disabled until an item is selected
        cancel_box = QPushButton("Cancel")
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(cancel_box)
        btn_row.addWidget(btn_box)
        pk_layout.addLayout(btn_row)
        cancel_box.clicked.connect(picker.reject)
        btn_box.clicked.connect(picker.accept)
        list_widget.currentItemChanged.connect(lambda cur, _: btn_box.setEnabled(cur is not None))

        if picker.exec() != QDialog.DialogCode.Accepted:
            return

        selected = list_widget.currentItem()
        if not selected:
            return

        entry = selected.data(Qt.ItemDataRole.UserRole)
        old_body = entry.get("response_body") or ""
        # Use the actual response body so large responses that were never "Load Full"-ed
        # are still diffed correctly instead of producing an empty comparison.
        displayed = self.body_text.toPlainText()
        new_body = displayed if displayed else self._pretty_body(self.current_response)

        old_lines = old_body.splitlines(keepends=True)
        new_lines = new_body.splitlines(keepends=True)
        diff_lines = list(difflib.unified_diff(
            old_lines, new_lines,
            fromfile="History", tofile="Current",
            lineterm="",
        ))
        diff_text = "".join(diff_lines) if diff_lines else "(No differences)"

        # Diff viewer dialog
        diff_dlg = QDialog(self)
        diff_dlg.setWindowTitle("Response Body Diff")
        diff_dlg.setMinimumSize(700, 500)
        dv_layout = QVBoxLayout(diff_dlg)
        diff_editor = QPlainTextEdit()
        diff_editor.setReadOnly(True)
        diff_editor.setFont(get_mono_font())
        diff_editor.setPlainText(diff_text)
        dv_layout.addWidget(diff_editor, 1)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(diff_dlg.accept)
        dv_layout.addWidget(close_btn)
        diff_dlg.exec()

    def _toggle_word_wrap(self) -> None:
        """Toggle line wrapping in the response body text view."""
        if self.body_text.lineWrapMode() == QTextEdit.LineWrapMode.NoWrap:
            self.body_text.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        else:
            self.body_text.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)

    def _on_timings_toggled(self, checked: bool) -> None:
        """Show/hide the timing breakdown label."""
        self._timings_toggle.setText("▼ Timings" if checked else "▶ Timings")
        self._timings_label.setVisible(checked)

    def _load_large_body(self) -> None:
        """User confirmed loading a large body (#10)."""
        if self.current_response is not None:
            self._body_warning.setVisible(False)
            self.body_text.set_code(self._pretty_body(self.current_response))

    def _on_jsonpath_filter(self, filtered_text: Optional[str]) -> None:
        """Receive filtered JSON text from the SearchBar and update the body view.

        If filtered_text is None the original body representation is restored.
        """
        try:
            if filtered_text is None:
                # Restore the original body view for the current response
                if self.current_response is None:
                    self.body_text.clear()
                    return
                if self.current_response.size > self._LARGE_BODY_THRESHOLD and self._body_warning.isVisible():
                    # Large-body warning was active and full body not loaded
                    self.body_text.clear()
                else:
                    self.body_text.set_code(self._pretty_body(self.current_response))
            else:
                # Show the filtered JSON representation
                self._body_warning.setVisible(False)
                self.body_text.set_code(filtered_text)
        except Exception:
            # On any error, fall back to original representation
            try:
                if self.current_response is not None:
                    self.body_text.set_code(self._pretty_body(self.current_response))
                else:
                    self.body_text.clear()
            except Exception:
                self.body_text.clear()

    def _on_hdrs_filter_changed(self, text: str) -> None:
        """Filter the response headers table (#8)."""
        self.resp_headers_table.filter(text)
        visible = self.resp_headers_table.rowCount()
        total = len(self.resp_headers_table._all_headers)
        if text.strip():
            self._hdrs_count_label.setText(f"{visible}/{total}")
        else:
            self._hdrs_count_label.setText(str(total))

    def _apply_highlighter(self, content_type: str) -> None:
        """Swap the syntax highlighter to match the response content-type."""
        ct = content_type.lower()
        if "json" in ct:
            new_cls = JsonHighlighter
        elif "xml" in ct or "html" in ct or "svg" in ct:
            new_cls = XmlHighlighter
        elif "yaml" in ct:
            new_cls = YamlHighlighter
        else:
            new_cls = None

        # Only rebuild if the highlighter type changes
        current_cls = type(self._body_highlighter) if self._body_highlighter else None
        if current_cls is not new_cls:
            if self._body_highlighter is not None:
                self._body_highlighter.setDocument(None)
            self._body_highlighter = new_cls(self.body_text.document()) if new_cls else None

    def _pretty_body(self, response: Response) -> str:
        if response.is_json:
            try:
                return json.dumps(response.json(), indent=2, ensure_ascii=False)
            except Exception:
                pass
        ct = response.headers.get("content-type", "").lower()
        if "xml" in ct or "html" in ct or "svg" in ct:
            try:
                import xml.dom.minidom
                return xml.dom.minidom.parseString(response.text.encode()).toprettyxml(indent="  ")
            except Exception:
                pass
        return response.text

    @staticmethod
    def _try_pretty_json(text: str) -> str:
        try:
            return json.dumps(json.loads(text), indent=2, ensure_ascii=False)
        except Exception:
            return text

    @staticmethod
    def _format_size(size_bytes: float) -> str:
        for unit in ("B", "KB", "MB", "GB"):
            if size_bytes < 1024.0:
                return f"{size_bytes:.1f} {unit}"
            size_bytes /= 1024.0
        return f"{size_bytes:.1f} TB"

    def _copy_body(self) -> None:
        text = self.body_text.toPlainText()
        if text:
            QApplication.clipboard().setText(text)
            try:
                self.window().statusBar().showMessage("Body copied to clipboard", 4000)
            except Exception:
                pass

    def _download_body(self) -> None:
        """Save the response body to a file chosen by the user."""
        if self.current_response is None:
            return

        # Suggest a filename based on URL path
        url = self.current_response.request.url
        suggested = os.path.basename(url.rstrip("/").split("?")[0]) or "response"
        ct = self.current_response.headers.get("content-type", "")
        if not os.path.splitext(suggested)[1]:
            if "json" in ct:
                suggested += ".json"
            elif "xml" in ct:
                suggested += ".xml"
            elif "html" in ct:
                suggested += ".html"
            elif "yaml" in ct:
                suggested += ".yaml"
            else:
                suggested += ".txt"

        path, _ = QFileDialog.getSaveFileName(self, "Save Response Body", suggested)
        if not path:
            return

        try:
            # Write raw bytes so large/unloaded bodies and binary content are
            # saved correctly — the displayed text may be empty or re-encoded.
            body_bytes = self.current_response.body or b""
            with open(path, "wb") as f:
                f.write(body_bytes)
            try:
                self.window().statusBar().showMessage(f"Response saved to {path}", 5000)
            except Exception:
                pass
        except Exception as exc:
            QMessageBox.warning(self, "Save Failed", f"Could not save response: {exc}")

    def _open_search(self) -> None:
        """Activate Ctrl+F search bar (switch to Body tab first)."""
        # Ensure raw body is visible when opening search
        self._switch_to_raw_view()
        self._search_bar.show_and_focus()

    def _on_view_selected(self, which: str) -> None:
        """Handle selection from the View menu (raw or json)."""
        self._prefer_json_view = (which == "json")
        if which == "json":
            self._switch_to_json_view()
        else:
            self._switch_to_raw_view()

    def _switch_to_raw_view(self) -> None:
        self.tabs.setTabVisible(self._body_tab_idx, True)
        self.tabs.setCurrentIndex(self._body_tab_idx)
        self._view_raw_act.setChecked(True)
        self._view_json_act.setChecked(False)

    def _switch_to_json_view(self) -> None:
        self.tabs.setTabVisible(self._json_tab_idx, True)
        self.tabs.setCurrentIndex(self._json_tab_idx)
        self._view_raw_act.setChecked(False)
        self._view_json_act.setChecked(True)

    def _copy_as_code(self, fmt: str) -> None:
        """Copy client code for the current request in *fmt* to the clipboard."""
        if self.current_response is None:
            return
        if fmt == "cURL":
            self._copy_as_curl()
            return
        try:
            code = generate_code(fmt, self.current_response.request)
            QApplication.clipboard().setText(code)
            try:
                self.window().statusBar().showMessage(
                    f"{fmt} code copied to clipboard", 4000
                )
            except Exception:
                pass
        except Exception as exc:
            try:
                self.window().statusBar().showMessage(f"Code gen error: {exc}", 5000)
            except Exception:
                pass

    def _view_code_dialog(self) -> None:
        """Open a dialog to view generated client code."""
        if self.current_response is None:
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("Generated Code")
        dialog.setMinimumSize(620, 420)
        dlg_layout = QVBoxLayout(dialog)

        top_row = QHBoxLayout()
        top_row.addWidget(QLabel("Format:"))
        fmt_combo = QComboBox()
        for f in list(GENERATORS.keys()) + ["cURL"]:
            fmt_combo.addItem(f)
        top_row.addWidget(fmt_combo)
        top_row.addStretch()
        copy_dlg_btn = QPushButton("Copy")
        copy_dlg_btn.setFixedWidth(70)
        top_row.addWidget(copy_dlg_btn)
        dlg_layout.addLayout(top_row)

        code_editor = QPlainTextEdit()
        code_editor.setReadOnly(True)
        code_editor.setFont(get_mono_font())
        code_editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        dlg_layout.addWidget(code_editor, 1)

        def _update_code() -> None:
            fmt = fmt_combo.currentText()
            if fmt == "cURL":
                code_editor.setPlainText(self._build_curl_command(self.current_response))
            else:
                try:
                    code_editor.setPlainText(
                        generate_code(fmt, self.current_response.request)
                    )
                except Exception as exc:
                    code_editor.setPlainText(f"# Error generating code: {exc}")

        fmt_combo.currentIndexChanged.connect(_update_code)
        _update_code()

        copy_dlg_btn.clicked.connect(
            lambda: QApplication.clipboard().setText(code_editor.toPlainText())
        )
        dialog.exec()

    @staticmethod
    def _build_curl_command(response: Response) -> str:
        """Return a shell-safe cURL command string for *response*'s request."""
        req  = response.request
        hdrs = response.sent_headers or req.headers or {}
        url  = response.sent_url or req.url

        parts = ["curl", "-X", req.method]
        for k, v in hdrs.items():
            if not k.startswith(":"):
                parts.extend(["-H", f"{k}: {v}"])
        if req.body:
            parts.extend(["-d", req.body])
        if hasattr(req, "verify_ssl") and not req.verify_ssl:
            parts.append("--insecure")
        parts.append(url)

        if os.name == "nt":
            escaped = []
            for p in parts:
                if any(c in p for c in " \t\"'&|<>^") or ":" in p:
                    escaped.append(f'"{p}"')
                else:
                    escaped.append(p)
            return " ^\n  ".join(escaped)
        return " \\\n  ".join(shlex.quote(p) for p in parts)

    def _copy_as_curl(self) -> None:
        if self.current_response is None:
            return
        QApplication.clipboard().setText(self._build_curl_command(self.current_response))
        try:
            self.window().statusBar().showMessage("cURL command copied to clipboard", 4000)
        except Exception:
            pass

    def clear(self) -> None:
        self.status_label.setText("No response yet")
        self.status_label.setStyleSheet(f"font-weight: bold; color: {Colors.FG_MUTED};")
        self.time_label.clear()
        self.size_label.clear()
        self.body_text.clear()
        self._body_warning.setVisible(False)
        self._timings_toggle.setVisible(False)
        self._timings_label.setVisible(False)
        self._timings_toggle.setChecked(False)
        self._timings_toggle.setText("▶ Timings")
        self._hdrs_search.clear()
        self._hdrs_count_label.clear()
        self.resp_headers_table.setRowCount(0)
        self.resp_headers_table._all_headers = {}
        self._cookies_table.setRowCount(0)
        for i in range(self.tabs.count()):
            if self.tabs.tabText(i).startswith("Cookies"):
                self.tabs.setTabText(i, "Cookies")
                break
        self.sent_headers_table.setRowCount(0)
        self.sent_body_text.clear()
        self.sent_method_label.setText("—")
        self.sent_url_label.setText("—")
        self.current_response = None
        # Clear JSON tab and search-bar JSONPath context
        try:
            self._json_tree.clear()
        except Exception:
            pass
        self._search_bar.set_json_doc(None)
        # Clear Intelligence tab
        try:
            self.intelligence_panel.clear()
        except Exception:
            pass
