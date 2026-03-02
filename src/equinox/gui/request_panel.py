"""Request builder panel."""

import logging
import os
from typing import Optional, Dict

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QComboBox,
    QLineEdit,
    QPushButton,
    QTabWidget,
    QPlainTextEdit,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QLabel,
    QGroupBox,
    QMessageBox,
    QDialog,
    QDialogButtonBox,
    QCompleter,
    QSplitter,
    QFileDialog,
    QCheckBox,
    QDoubleSpinBox,
    QFormLayout,
)
from PyQt6.QtCore import pyqtSignal, Qt, QThread, QTimer, QStringListModel
from PyQt6.QtGui import QFont, QKeySequence, QShortcut

from equinox.gui.theme import Colors, get_mono_font, get_font_size
from equinox.gui.widgets import UrlLineEdit, KeyValueTable, CheckableKeyValueTable, JsonBodyEditor
from equinox.core.client import HTTPClient
from equinox.core.request import Request, Response
from equinox.core.error_enrichment import RichError, enrich_exception
from equinox.storage import Database, HistoryManager

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30.0


# ─────────────────────────────────────────────────────────────────────────────
# Assertion evaluation — delegate to core module (also used by CLI)
# ─────────────────────────────────────────────────────────────────────────────

from equinox.core.assertions import evaluate_assertion as _evaluate_assertion


# ─────────────────────────────────────────────────────────────────────────────
# Background worker
# ─────────────────────────────────────────────────────────────────────────────

class RequestWorker(QThread):
    """Worker thread for sending HTTP requests.

    Emits ``finished(result)`` where *result* is either a :class:`Response`
    or an :class:`Exception`.  ``cancel()`` marks the result as stale so the
    GUI ignores it even if the TCP connection completes.
    """

    finished = pyqtSignal(object)

    def __init__(self, request: Request, parent=None, cookie_manager=None):
        super().__init__(parent)
        self.request = request
        self._cancelled = False
        self._cookie_manager = cookie_manager

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        try:
            from PyQt6.QtCore import QSettings as _QS
            _s = _QS("Equinox", "Equinox")
            _ph = (_s.value("proxy/host") or "").strip()
            _pp = int(_s.value("proxy/port") or 0)
            _proxy = f"http://{_ph}:{_pp}" if _ph and _pp else None
            client = HTTPClient(
                cookie_manager=self._cookie_manager,
                timeout=getattr(self.request, "timeout", DEFAULT_TIMEOUT),
                verify_ssl=getattr(self.request, "verify_ssl", True),
                follow_redirects=getattr(self.request, "follow_redirects", True),
                proxy=_proxy,
            )
            response = client.send(self.request)
            if not self._cancelled:
                self.finished.emit(response)
        except Exception as exc:
            if not self._cancelled:
                self.finished.emit(enrich_exception(exc))


# ─────────────────────────────────────────────────────────────────────────────
# Widgets are now in equinox.gui.widgets — imported at module level above.
# ─────────────────────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────────────────
# Benchmark dialog
# ─────────────────────────────────────────────────────────────────────────────

class BenchmarkDialog(QDialog):
    """Run the current request N times and display timing statistics."""

    def __init__(self, request: Request, parent=None, cookie_manager=None):
        super().__init__(parent)
        self._request = request
        self._cookie_manager = cookie_manager
        self.setWindowTitle("Benchmark")
        self.setMinimumSize(420, 340)
        self._times: list = []
        self._errors: int = 0
        self._init_ui()

    def _init_ui(self) -> None:
        from PyQt6.QtWidgets import QProgressBar, QSpinBox as _Spin
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        form = QFormLayout()
        self._count_spin = _Spin()
        self._count_spin.setRange(1, 1000)
        self._count_spin.setValue(10)
        form.addRow("Number of requests:", self._count_spin)
        layout.addLayout(form)

        self._run_btn = QPushButton("Run Benchmark")
        self._run_btn.clicked.connect(self._run)
        layout.addWidget(self._run_btn)

        self._progress = QProgressBar()
        self._progress.setVisible(False)
        layout.addWidget(self._progress)

        self._results = QPlainTextEdit()
        self._results.setReadOnly(True)
        self._results.setFont(get_mono_font())
        self._results.setPlaceholderText("Results will appear here after running.")
        layout.addWidget(self._results, 1)

        bottom_row = QHBoxLayout()
        self._export_btn = QPushButton("Export…")
        self._export_btn.setEnabled(False)
        self._export_btn.setToolTip("Export benchmark results to CSV or JSON")
        self._export_btn.clicked.connect(self._export_results)
        close_btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_btns.rejected.connect(self.reject)
        bottom_row.addWidget(self._export_btn)
        bottom_row.addStretch()
        bottom_row.addWidget(close_btns)
        layout.addLayout(bottom_row)

    def _run(self) -> None:
        import time
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtCore import QSettings

        n = self._count_spin.value()
        self._progress.setMaximum(n)
        self._progress.setValue(0)
        self._progress.setVisible(True)
        self._run_btn.setEnabled(False)
        self._results.setPlainText("Running…")
        QApplication.processEvents()

        s = QSettings("Equinox", "Equinox")
        ph = (s.value("proxy/host") or "").strip()
        pp = int(s.value("proxy/port") or 0)
        proxy = f"http://{ph}:{pp}" if ph and pp else None

        times: list = []
        errors = 0

        for i in range(n):
            try:
                client = HTTPClient(
                    cookie_manager=self._cookie_manager,
                    timeout=getattr(self._request, "timeout", DEFAULT_TIMEOUT),
                    verify_ssl=getattr(self._request, "verify_ssl", True),
                    follow_redirects=getattr(self._request, "follow_redirects", True),
                    proxy=proxy,
                )
                t0 = time.monotonic()
                client.send(self._request)
                times.append(time.monotonic() - t0)
            except Exception:
                errors += 1
            self._progress.setValue(i + 1)
            QApplication.processEvents()

        self._run_btn.setEnabled(True)
        self._progress.setVisible(False)

        if not times:
            self._results.setPlainText(f"All {n} request(s) failed.")
            return

        self._times = times
        self._errors = errors

        times_s = sorted(times)
        n_ok = len(times_s)
        avg = sum(times_s) / n_ok
        p95 = times_s[max(0, int(n_ok * 0.95) - 1)]
        p99 = times_s[max(0, int(n_ok * 0.99) - 1)]

        self._results.setPlainText(
            f"Requests : {n}\n"
            f"Success  : {n_ok}\n"
            f"Errors   : {errors}\n"
            f"\n"
            f"Min      : {times_s[0] * 1000:.1f} ms\n"
            f"Avg      : {avg * 1000:.1f} ms\n"
            f"Max      : {times_s[-1] * 1000:.1f} ms\n"
            f"p95      : {p95 * 1000:.1f} ms\n"
            f"p99      : {p99 * 1000:.1f} ms\n"
        )
        self._export_btn.setEnabled(True)

    def _export_results(self) -> None:
        """Export benchmark timing data to CSV or JSON chosen by the user."""
        if not self._times:
            return

        import csv
        import json as _json
        from datetime import datetime as _dt

        times_ms = [round(t * 1000, 3) for t in self._times]
        times_s  = sorted(self._times)
        n_ok     = len(times_s)
        avg      = sum(times_s) / n_ok

        summary = {
            "url":      self._request.url,
            "method":   self._request.method,
            "run_at":   _dt.now().isoformat(timespec="seconds"),
            "requests": n_ok + self._errors,
            "success":  n_ok,
            "errors":   self._errors,
            "min_ms":   round(times_s[0] * 1000, 3),
            "avg_ms":   round(avg * 1000, 3),
            "max_ms":   round(times_s[-1] * 1000, 3),
            "p95_ms":   round(times_s[max(0, int(n_ok * 0.95) - 1)] * 1000, 3),
            "p99_ms":   round(times_s[max(0, int(n_ok * 0.99) - 1)] * 1000, 3),
            "iterations": times_ms,
        }

        path, selected_filter = QFileDialog.getSaveFileName(
            self, "Export Benchmark Results", "benchmark.json",
            "JSON files (*.json);;CSV files (*.csv)",
        )
        if not path:
            return

        try:
            if selected_filter.startswith("CSV") or path.lower().endswith(".csv"):
                with open(path, "w", newline="", encoding="utf-8") as f:
                    writer = csv.writer(f)
                    writer.writerow(["iteration", "elapsed_ms"])
                    for idx, ms in enumerate(times_ms, 1):
                        writer.writerow([idx, ms])
            else:
                with open(path, "w", encoding="utf-8") as f:
                    _json.dump(summary, f, indent=2)
        except Exception as exc:
            QMessageBox.warning(self, "Export Failed", f"Could not write file: {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# Request panel
# ─────────────────────────────────────────────────────────────────────────────

class RequestPanel(QWidget):
    """Panel for building and sending HTTP requests."""

    response_received = pyqtSignal(object)
    request_sent      = pyqtSignal(object)

    # ── Accessor helpers ───────────────────────────────────────────────

    @property
    def _logging_panel(self):
        """Return the main window's LoggingPanel, or None if unavailable."""
        try:
            win = self.window()
            return getattr(win, "logging_panel", None)
        except Exception:
            return None

    def _status_message(self, text: str, timeout_ms: int = 5000) -> None:
        """Show a message in the main window status bar (best-effort)."""
        try:
            self.window().statusBar().showMessage(text, timeout_ms)
        except Exception:
            pass

    def __init__(self, db: Database, parent=None, cookie_manager=None):
        super().__init__(parent)
        self.db = db
        self._cookie_manager = cookie_manager
        self.current_request: Optional[Request] = None
        self._auth = None
        self._inherited_auth = None
        self._inherited_auth_source = None
        self._session_vars: Dict[str, str] = {}
        self._worker: Optional[RequestWorker] = None
        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.setInterval(100)
        self._elapsed_timer.timeout.connect(self._tick_elapsed)
        self._elapsed_secs = 0.0
        self._dirty = False
        self._init_ui()
        self._setup_dirty_tracking()
        self._setup_url_completer()

        # Ctrl+Enter sends from anywhere in the panel (#8)
        send_shortcut = QShortcut(QKeySequence("Ctrl+Return"), self)
        send_shortcut.activated.connect(self._send_request)

        # Ctrl+Shift+F formats JSON body (#6)
        fmt_shortcut = QShortcut(QKeySequence("Ctrl+Shift+F"), self)
        fmt_shortcut.activated.connect(self._format_json_body)

    # ── Dirty-flag tracking (#3) ──────────────────────────────────────

    def is_dirty(self) -> bool:
        return self._dirty

    def _mark_dirty(self):
        self._dirty = True

    def _clear_dirty(self):
        self._dirty = False

    def _setup_dirty_tracking(self):
        """Connect change signals on all editor widgets to mark dirty."""
        self.url_input.textChanged.connect(self._mark_dirty)
        self.method_combo.currentIndexChanged.connect(self._mark_dirty)
        self.body_text.textChanged.connect(self._mark_dirty)
        self.body_text.textChanged.connect(self._update_tab_labels)
        self.body_type_combo.currentIndexChanged.connect(self._mark_dirty)
        self.headers_table.itemChanged.connect(self._mark_dirty)
        self.headers_table.itemChanged.connect(self._update_tab_labels)
        self.params_table.itemChanged.connect(self._mark_dirty)
        self.params_table.itemChanged.connect(self._update_tab_labels)
        self.params_table.itemChanged.connect(self._update_url_suffix)
        self.url_input.textChanged.connect(self._update_url_suffix)
        self._multipart_table.itemChanged.connect(self._mark_dirty)
        self._multipart_table.itemChanged.connect(self._update_tab_labels)
        self.timeout_spin.valueChanged.connect(self._mark_dirty)
        self.verify_ssl_check.stateChanged.connect(self._mark_dirty)
        self.follow_redirects_check.stateChanged.connect(self._mark_dirty)
        self.notes_editor.textChanged.connect(self._mark_dirty)
        self._gql_query.textChanged.connect(self._mark_dirty)
        self._gql_vars.textChanged.connect(self._mark_dirty)

    # ── URL auto-complete from history (#6) ───────────────────────────

    def _setup_url_completer(self):
        self._url_model = QStringListModel(self)
        completer = QCompleter(self._url_model, self)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        completer.setMaxVisibleItems(12)
        self.url_input.setCompleter(completer)
        self._refresh_url_completer()

    def _refresh_url_completer(self):
        """Populate the completer model from recent history URLs."""
        try:
            mgr = HistoryManager(self.db)
            entries = mgr.list_history(limit=200)
            urls = list(dict.fromkeys(e["url"] for e in entries))  # deduplicate, keep order
            self._url_model.setStringList(urls)
        except Exception:
            pass

    # ── UI construction ───────────────────────────────────────────────

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 4)
        layout.setSpacing(4)

        # Request line
        req_line = QHBoxLayout()
        req_line.setSpacing(4)

        self.method_combo = QComboBox()
        self.method_combo.addItems(["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
        self.method_combo.setFixedWidth(90)

        self.url_input = UrlLineEdit()
        self.url_input.setPlaceholderText("https://api.example.com/v1/resource  ·  {{VAR}} for variables  ·  Ctrl+N = new")
        self.url_input.returnPressed.connect(self._send_request)

        self.send_button = QPushButton("Send")
        self.send_button.setObjectName("sendBtn")
        self.send_button.setFixedWidth(80)
        self.send_button.clicked.connect(self._send_request)
        self.send_button.setDefault(True)

        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setObjectName("cancelBtn")
        self.cancel_button.setFixedWidth(70)
        self.cancel_button.clicked.connect(self._cancel_request)
        self.cancel_button.setVisible(False)

        req_line.addWidget(self.method_combo)
        req_line.addWidget(self.url_input, 1)
        req_line.addWidget(self.send_button)
        req_line.addWidget(self.cancel_button)
        layout.addLayout(req_line)

        # Pre-flight validation warning banner (hidden by default)
        self._preflight_banner = QWidget()
        self._preflight_banner.setObjectName("preflightBanner")
        pf_row = QHBoxLayout(self._preflight_banner)
        pf_row.setContentsMargins(6, 2, 4, 2)
        pf_row.setSpacing(6)
        self._preflight_label = QLabel("")
        self._preflight_label.setStyleSheet(f"color: {Colors.AMBER}; font-weight: bold;")
        self._preflight_label.setWordWrap(True)
        from PyQt6.QtWidgets import QToolButton
        pf_dismiss = QToolButton()
        pf_dismiss.setText("✕")
        pf_dismiss.setFixedSize(20, 20)
        pf_dismiss.setStyleSheet("border: none;")
        pf_dismiss.clicked.connect(lambda: self._preflight_banner.setVisible(False))
        pf_row.addWidget(self._preflight_label, 1)
        pf_row.addWidget(pf_dismiss)
        self._preflight_banner.setVisible(False)
        layout.addWidget(self._preflight_banner)

        # Tabs
        self.tabs = QTabWidget()

        # Headers tab: CheckableKeyValueTable + bulk actions + common presets
        headers_widget = QWidget()
        headers_layout = QVBoxLayout(headers_widget)
        headers_layout.setContentsMargins(0, 2, 0, 0)
        headers_layout.setSpacing(2)

        headers_toolbar = QHBoxLayout()
        enable_all_btn = QPushButton("Enable All")
        enable_all_btn.setFixedWidth(80)
        enable_all_btn.setToolTip("Enable all header rows")
        enable_all_btn.clicked.connect(lambda: self._headers_set_all(True))

        disable_all_btn = QPushButton("Disable All")
        disable_all_btn.setFixedWidth(82)
        disable_all_btn.setToolTip("Disable all header rows")
        disable_all_btn.clicked.connect(lambda: self._headers_set_all(False))

        from PyQt6.QtWidgets import QToolButton, QMenu
        presets_btn = QToolButton()
        presets_btn.setText("Presets ▾")
        presets_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        presets_btn.setToolTip("Insert a common header")
        presets_menu = QMenu(presets_btn)
        _HEADER_PRESETS = [
            ("Content-Type: application/json",         "Content-Type", "application/json"),
            ("Content-Type: application/xml",          "Content-Type", "application/xml"),
            ("Content-Type: application/x-www-form-urlencoded",
             "Content-Type", "application/x-www-form-urlencoded"),
            ("Content-Type: multipart/form-data",      "Content-Type", "multipart/form-data"),
            ("Content-Type: text/plain",               "Content-Type", "text/plain"),
            None,  # separator
            ("Accept: application/json",               "Accept", "application/json"),
            ("Accept: application/xml",                "Accept", "application/xml"),
            ("Accept: */*",                            "Accept", "*/*"),
            None,
            ("Authorization: Bearer …",                "Authorization", "Bearer "),
            ("X-API-Key: …",                           "X-API-Key", ""),
            None,
            ("Cache-Control: no-cache",                "Cache-Control", "no-cache"),
            ("User-Agent: Equinox/1.0",                "User-Agent", "Equinox/1.0"),
        ]
        for preset in _HEADER_PRESETS:
            if preset is None:
                presets_menu.addSeparator()
            else:
                label, key, value = preset
                act = presets_menu.addAction(label)
                act.triggered.connect(
                    lambda checked, k=key, v=value: self._insert_header_preset(k, v)
                )
        presets_btn.setMenu(presets_menu)

        headers_toolbar.addWidget(enable_all_btn)
        headers_toolbar.addWidget(disable_all_btn)
        headers_toolbar.addStretch()
        headers_toolbar.addWidget(presets_btn)
        headers_layout.addLayout(headers_toolbar)

        self.headers_table = CheckableKeyValueTable()
        headers_layout.addWidget(self.headers_table, 1)
        self.tabs.addTab(headers_widget, "Headers")

        # Params tab: CheckableKeyValueTable + bulk actions
        params_widget = QWidget()
        params_layout = QVBoxLayout(params_widget)
        params_layout.setContentsMargins(0, 2, 0, 0)
        params_layout.setSpacing(2)

        params_toolbar = QHBoxLayout()
        params_enable_btn = QPushButton("Enable All")
        params_enable_btn.setFixedWidth(80)
        params_enable_btn.setToolTip("Enable all query parameter rows")
        params_enable_btn.clicked.connect(lambda: self._params_set_all(True))

        params_disable_btn = QPushButton("Disable All")
        params_disable_btn.setFixedWidth(82)
        params_disable_btn.setToolTip("Disable all query parameter rows")
        params_disable_btn.clicked.connect(lambda: self._params_set_all(False))

        params_toolbar.addWidget(params_enable_btn)
        params_toolbar.addWidget(params_disable_btn)
        params_toolbar.addStretch()
        params_layout.addLayout(params_toolbar)

        self.params_table = CheckableKeyValueTable()
        params_layout.addWidget(self.params_table, 1)
        self.tabs.addTab(params_widget, "Params")

        body_widget = QWidget()
        body_layout = QVBoxLayout(body_widget)
        body_layout.setContentsMargins(0, 4, 0, 0)

        body_type_bar = QHBoxLayout()
        self.body_type_combo = QComboBox()
        self.body_type_combo.addItems(
            ["none", "raw (JSON)", "raw (XML)", "raw (text)", "form-urlencoded", "multipart/form-data", "GraphQL"]
        )
        self.body_type_combo.currentIndexChanged.connect(self._on_body_type_changed)
        body_type_bar.addWidget(QLabel("Type:"))
        body_type_bar.addWidget(self.body_type_combo)

        # #6 — Format JSON button (only visible for raw JSON type)
        self._fmt_json_btn = QPushButton("Format JSON")
        self._fmt_json_btn.setFixedWidth(95)
        self._fmt_json_btn.setToolTip("Pretty-print the JSON body (Ctrl+Shift+F)")
        self._fmt_json_btn.clicked.connect(self._format_json_body)
        self._fmt_json_btn.setVisible(False)
        body_type_bar.addWidget(self._fmt_json_btn)

        body_type_bar.addStretch()
        body_layout.addLayout(body_type_bar)

        self.body_text = JsonBodyEditor()
        self.body_text.setPlaceholderText('{ "key": "value" }')
        self.body_text.setFont(get_mono_font())
        # #4 — JSON syntax highlighting
        from equinox.gui.syntax_highlighter import JsonHighlighter
        self._body_highlighter = JsonHighlighter(self.body_text.document())
        body_layout.addWidget(self.body_text)

        # #7 — Multipart/form-data table (shown instead of body_text)
        self._multipart_table = QTableWidget(0, 3)
        self._multipart_table.setHorizontalHeaderLabels(["Key", "Type", "Value / File Path"])
        _mp_hdr = self._multipart_table.horizontalHeader()
        _mp_hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        _mp_hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        _mp_hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._multipart_table.horizontalHeader().setDefaultSectionSize(140)
        self._multipart_table.verticalHeader().setVisible(False)
        self._multipart_table.setAlternatingRowColors(True)
        self._multipart_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._multipart_table.setVisible(False)
        body_layout.addWidget(self._multipart_table)

        mp_btns_row = QHBoxLayout()
        self._mp_add_btn = QPushButton("+ Field")
        self._mp_add_btn.setFixedWidth(68)
        self._mp_add_btn.clicked.connect(self._multipart_add_row)
        self._mp_remove_btn = QPushButton("− Remove")
        self._mp_remove_btn.setFixedWidth(80)
        self._mp_remove_btn.clicked.connect(self._multipart_remove_row)
        self._mp_file_btn = QPushButton("Browse File…")
        self._mp_file_btn.setFixedWidth(100)
        self._mp_file_btn.clicked.connect(self._multipart_browse_file)
        self._mp_file_btn.setToolTip("Select a file to upload for the selected row")
        mp_btns_row.addWidget(self._mp_add_btn)
        mp_btns_row.addWidget(self._mp_remove_btn)
        mp_btns_row.addWidget(self._mp_file_btn)
        mp_btns_row.addStretch()
        self._mp_btns_widget = QWidget()
        self._mp_btns_widget.setLayout(mp_btns_row)
        self._mp_btns_widget.setVisible(False)
        body_layout.addWidget(self._mp_btns_widget)

        # GraphQL editor — split view: query on top, variables on bottom
        self._gql_widget = QWidget()
        _gql_layout = QVBoxLayout(self._gql_widget)
        _gql_layout.setContentsMargins(0, 4, 0, 0)
        _gql_splitter = QSplitter(Qt.Orientation.Vertical)

        _q_group = QGroupBox("Query")
        _q_lay = QVBoxLayout(_q_group)
        _q_lay.setContentsMargins(4, 6, 4, 4)
        self._gql_query = QPlainTextEdit()
        self._gql_query.setPlaceholderText(
            "query {\n  users {\n    id\n    name\n  }\n}"
        )
        self._gql_query.setFont(get_mono_font())
        _q_lay.addWidget(self._gql_query)

        _v_group = QGroupBox("Variables (JSON, optional)")
        _v_lay = QVBoxLayout(_v_group)
        _v_lay.setContentsMargins(4, 6, 4, 4)
        self._gql_vars = QPlainTextEdit()
        self._gql_vars.setPlaceholderText('{\n  "id": 1\n}')
        self._gql_vars.setFont(get_mono_font())
        _v_lay.addWidget(self._gql_vars)

        _gql_splitter.addWidget(_q_group)
        _gql_splitter.addWidget(_v_group)
        _gql_splitter.setSizes([200, 120])
        _gql_layout.addWidget(_gql_splitter, 1)
        self._gql_widget.setVisible(False)
        body_layout.addWidget(self._gql_widget)

        self.tabs.addTab(body_widget, "Body")

        auth_widget = self._create_auth_tab()
        self.tabs.addTab(auth_widget, "Auth")

        captures_widget = self._create_captures_tab()
        self.tabs.addTab(captures_widget, "Captures")

        scripts_widget = self._create_scripts_tab()
        self.tabs.addTab(scripts_widget, "Scripts")

        # Notes tab — uses the existing Request.description field
        notes_widget = QWidget()
        notes_layout = QVBoxLayout(notes_widget)
        notes_layout.setContentsMargins(4, 6, 4, 4)
        notes_layout.addWidget(QLabel("Notes / description for this request:"))
        self.notes_editor = QPlainTextEdit()
        self.notes_editor.setPlaceholderText(
            "Add notes, cURL examples, API docs links, or any context about this request…"
        )
        notes_layout.addWidget(self.notes_editor, 1)
        self.tabs.addTab(notes_widget, "Notes")

        # Assertions tab
        assertions_widget = self._create_assertions_tab()
        self.tabs.addTab(assertions_widget, "Assertions")

        layout.addWidget(self.tabs, 1)

        # Bottom bar
        bottom = QHBoxLayout()
        paste_curl_btn = QPushButton("Paste cURL…")
        paste_curl_btn.setToolTip("Import a request from a cURL command (from clipboard or typed)")
        paste_curl_btn.clicked.connect(self._import_from_curl)
        benchmark_btn = QPushButton("Benchmark…")
        benchmark_btn.setToolTip("Send this request N times and display timing statistics")
        benchmark_btn.clicked.connect(self._open_benchmark)
        self.save_button = QPushButton("Save to Collection…")
        self.save_button.clicked.connect(self._save_request)
        bottom.addWidget(paste_curl_btn)
        bottom.addWidget(benchmark_btn)
        bottom.addStretch()
        bottom.addWidget(self.save_button)
        layout.addLayout(bottom)

    def _create_auth_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(6, 8, 6, 8)
        self.auth_type_label = QLabel("Auth: None")
        self.auth_type_label.setStyleSheet("font-weight: bold;")
        self.auth_details_label = QLabel("No authentication configured")
        self.auth_details_label.setObjectName("mutedLabel")
        self.auth_details_label.setWordWrap(True)
        self.auth_status_label = QLabel("")
        self.auth_status_label.setWordWrap(True)
        configure_btn = QPushButton("Configure Authentication…")
        configure_btn.clicked.connect(self._configure_auth)
        clear_btn = QPushButton("Clear Auth")
        clear_btn.clicked.connect(self._clear_auth)
        btn_row = QHBoxLayout()
        btn_row.addWidget(configure_btn)
        btn_row.addWidget(clear_btn)
        btn_row.addStretch()
        layout.addWidget(self.auth_type_label)
        layout.addWidget(self.auth_details_label)
        layout.addWidget(self.auth_status_label)
        layout.addLayout(btn_row)
        layout.addStretch()
        return w

    def _create_captures_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(0, 4, 0, 0)

        toolbar = QHBoxLayout()
        add_btn = QPushButton("+ Add")
        add_btn.setFixedWidth(64)
        add_btn.clicked.connect(self._captures_add_row)
        remove_btn = QPushButton("− Remove")
        remove_btn.setFixedWidth(80)
        remove_btn.clicked.connect(self._captures_remove_row)
        toolbar.addWidget(add_btn)
        toolbar.addWidget(remove_btn)
        toolbar.addStretch()
        layout.addLayout(toolbar)

        self.captures_table = QTableWidget(0, 4)
        self.captures_table.setHorizontalHeaderLabels(["Variable", "Source", "Path / Pattern", "Default"])
        hdr = self.captures_table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        self.captures_table.verticalHeader().setVisible(False)
        self.captures_table.setAlternatingRowColors(True)
        self.captures_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        layout.addWidget(self.captures_table)

        layout.addWidget(QLabel("Last capture results:"))
        self.captures_results_label = QLabel("—")
        self.captures_results_label.setFont(get_mono_font())
        self.captures_results_label.setWordWrap(True)
        self.captures_results_label.setObjectName("mutedLabel")
        layout.addWidget(self.captures_results_label)

        return w

    def _create_assertions_tab(self) -> QWidget:
        """Assertions tab — define pass/fail rules evaluated after each response."""
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(0, 4, 0, 0)

        toolbar = QHBoxLayout()
        add_btn = QPushButton("+ Add")
        add_btn.setFixedWidth(64)
        add_btn.clicked.connect(self._assertions_add_row)
        remove_btn = QPushButton("− Remove")
        remove_btn.setFixedWidth(80)
        remove_btn.clicked.connect(self._assertions_remove_row)
        toolbar.addWidget(add_btn)
        toolbar.addWidget(remove_btn)
        toolbar.addStretch()
        layout.addLayout(toolbar)

        self.assertions_table = QTableWidget(0, 3)
        self.assertions_table.setHorizontalHeaderLabels(["Type", "Field / Path", "Expected"])
        ahdr = self.assertions_table.horizontalHeader()
        ahdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        ahdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        ahdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.assertions_table.horizontalHeader().setDefaultSectionSize(160)
        self.assertions_table.verticalHeader().setVisible(False)
        self.assertions_table.setAlternatingRowColors(True)
        self.assertions_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        layout.addWidget(self.assertions_table)

        layout.addWidget(QLabel("Last assertion results:"))
        self.assertions_results_label = QLabel("—")
        self.assertions_results_label.setFont(get_mono_font())
        self.assertions_results_label.setWordWrap(True)
        self.assertions_results_label.setObjectName("mutedLabel")
        layout.addWidget(self.assertions_results_label)

        return w

    def _assertions_add_row(self) -> None:
        """Append a new empty assertion row to the table."""
        row = self.assertions_table.rowCount()
        self.assertions_table.insertRow(row)
        type_combo = QComboBox()
        type_combo.addItems([
            "status", "body_contains", "header_value", "jsonpath", "elapsed_lt"
        ])
        self.assertions_table.setCellWidget(row, 0, type_combo)
        self.assertions_table.setItem(row, 1, QTableWidgetItem(""))
        self.assertions_table.setItem(row, 2, QTableWidgetItem(""))

    def _assertions_remove_row(self) -> None:
        """Remove the currently selected assertion row(s)."""
        rows = sorted(
            {idx.row() for idx in self.assertions_table.selectedIndexes()},
            reverse=True,
        )
        for row in rows:
            self.assertions_table.removeRow(row)

    def _get_assertions(self) -> list:
        """Collect assertion rules from the assertions table."""
        rules = []
        for row in range(self.assertions_table.rowCount()):
            widget = self.assertions_table.cellWidget(row, 0)
            a_type = widget.currentText() if widget else "status"
            f_item = self.assertions_table.item(row, 1)
            e_item = self.assertions_table.item(row, 2)
            field    = f_item.text().strip() if f_item else ""
            expected = e_item.text().strip() if e_item else ""
            if expected:
                rules.append({"type": a_type, "field": field, "expected": expected})
        return rules

    def _set_assertions(self, rules: list) -> None:
        """Populate the assertions table from a list of rule dicts."""
        self.assertions_table.setRowCount(0)
        for rule in (rules or []):
            self._assertions_add_row()
            row = self.assertions_table.rowCount() - 1
            widget = self.assertions_table.cellWidget(row, 0)
            if widget:
                idx = widget.findText(rule.get("type", "status"))
                if idx >= 0:
                    widget.setCurrentIndex(idx)
            f_item = self.assertions_table.item(row, 1)
            e_item = self.assertions_table.item(row, 2)
            if f_item:
                f_item.setText(rule.get("field", ""))
            if e_item:
                e_item.setText(rule.get("expected", ""))

    def _evaluate_assertions(self, response) -> None:
        """Run assertion rules against the response and display results."""
        rules = self._get_assertions()
        if not rules:
            self.assertions_results_label.setText("—")
            return
        lines = []
        all_pass = True
        for rule in rules:
            passed, msg = _evaluate_assertion(rule, response)
            icon = "✓" if passed else "✗"
            lines.append(f"{icon} {msg}")
            if not passed:
                all_pass = False
        self.assertions_results_label.setText("\n".join(lines) if lines else "—")
        # Update the tab title with pass/fail summary
        passed_count = sum(1 for l in lines if l.startswith("✓"))
        total = len(lines)
        for i in range(self.tabs.count()):
            if self.tabs.tabText(i).startswith("Assertions"):
                label = f"Assertions ({passed_count}/{total})" if lines else "Assertions"
                self.tabs.setTabText(i, label)
                break

    def _create_scripts_tab(self) -> QWidget:
        """Single tab with Pre-request and Post-response script editors."""
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(4, 6, 4, 4)

        splitter = QSplitter(Qt.Orientation.Vertical)

        # ── Pre-request section ───────────────────────────────────────
        pre_group = QGroupBox("Pre-request Script")
        pre_layout = QVBoxLayout(pre_group)
        pre_layout.setContentsMargins(4, 6, 4, 4)
        self.pre_script_editor = QPlainTextEdit()
        self.pre_script_editor.setPlaceholderText(
            "# Runs before the request is sent\n"
            "# Available: request (dict), env (dict)\n"
            "# Example: env['timestamp'] = str(int(__import__('time').time()))"
        )
        self.pre_script_editor.setFont(QFont("Courier New", get_font_size()))
        self.pre_script_result = QLabel("")
        self.pre_script_result.setWordWrap(True)
        pre_layout.addWidget(self.pre_script_editor)
        pre_layout.addWidget(self.pre_script_result)
        splitter.addWidget(pre_group)

        # ── Post-response section ─────────────────────────────────────
        post_group = QGroupBox("Post-response Script")
        post_layout = QVBoxLayout(post_group)
        post_layout.setContentsMargins(4, 6, 4, 4)
        self.post_script_editor = QPlainTextEdit()
        self.post_script_editor.setPlaceholderText(
            "# Runs after response is received\n"
            "# Available: response (dict with status_code, headers, body, json), env (dict)\n"
            "# Example: env['user_id'] = str(response['json']['id'])"
        )
        self.post_script_editor.setFont(QFont("Courier New", get_font_size()))
        self.post_script_result = QLabel("")
        self.post_script_result.setWordWrap(True)
        post_layout.addWidget(self.post_script_editor)
        post_layout.addWidget(self.post_script_result)
        splitter.addWidget(post_group)

        splitter.setSizes([300, 300])
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(5)
        layout.addWidget(splitter, 1)

        # ── Python syntax highlighting for script editors ─────────────
        from equinox.gui.syntax_highlighter import PythonHighlighter
        self._pre_highlighter = PythonHighlighter(self.pre_script_editor.document())
        self._post_highlighter = PythonHighlighter(self.post_script_editor.document())

        # ── Collapsible cheat-sheet ───────────────────────────────────
        from PyQt6.QtWidgets import QPushButton as _PB
        cheat_toggle = _PB()
        cheat_toggle.setText("▶ Available variables & modules")
        cheat_toggle.setCheckable(True)
        cheat_toggle.setFlat(True)
        layout.addWidget(cheat_toggle)

        _CHEAT_TEXT = (
            "<b>Context objects</b><br>"
            "&nbsp;&nbsp;<code>env</code> — dict of active environment variables (read/write)<br>"
            "&nbsp;&nbsp;<code>request</code> — dict: method, url, headers, body (pre-script only)<br>"
            "&nbsp;&nbsp;<code>response</code> — dict: status_code, headers, body, json (post-script only)<br>"
            "<br><b>Allowed modules</b><br>"
            "&nbsp;&nbsp;json, re, base64, hashlib, hmac, datetime, time, math, uuid, urllib.parse"
        )
        cheat_label = QLabel(_CHEAT_TEXT)
        cheat_label.setTextFormat(Qt.TextFormat.RichText)
        cheat_label.setObjectName("mutedLabel")
        cheat_label.setVisible(False)
        cheat_label.setWordWrap(True)
        cheat_label.setContentsMargins(8, 2, 8, 4)
        layout.addWidget(cheat_label)
        cheat_toggle.toggled.connect(
            lambda checked: (
                cheat_label.setVisible(checked),
                cheat_toggle.setText("▼ Available variables & modules" if checked
                                     else "▶ Available variables & modules"),
            )
        )

        # ── Request Settings ──────────────────────────────────────────
        settings_group = QGroupBox("Request Settings")
        settings_form = QFormLayout(settings_group)
        settings_form.setContentsMargins(8, 8, 8, 8)

        self.timeout_spin = QDoubleSpinBox()
        self.timeout_spin.setRange(1.0, 300.0)
        self.timeout_spin.setValue(DEFAULT_TIMEOUT)
        self.timeout_spin.setSuffix(" s")
        self.timeout_spin.setDecimals(1)
        self.timeout_spin.setToolTip("Request timeout in seconds (1–300)")
        settings_form.addRow("Timeout:", self.timeout_spin)

        self.verify_ssl_check = QCheckBox("Verify SSL certificates")
        self.verify_ssl_check.setChecked(True)
        settings_form.addRow("", self.verify_ssl_check)

        self.follow_redirects_check = QCheckBox("Follow redirects")
        self.follow_redirects_check.setChecked(True)
        settings_form.addRow("", self.follow_redirects_check)

        layout.addWidget(settings_group)

        # ── Certificate fields ────────────────────────────────────────
        cert_group = QGroupBox("Client Certificate (optional)")
        cert_layout = QVBoxLayout(cert_group)
        cert_layout.setContentsMargins(4, 6, 4, 4)

        cert_row = QHBoxLayout()
        cert_row.addWidget(QLabel("Cert file:"))
        self.cert_path_input = QLineEdit()
        self.cert_path_input.setPlaceholderText("Path to .pem / .crt file")
        cert_browse = QPushButton("Browse…")
        cert_browse.setFixedWidth(70)
        cert_browse.clicked.connect(self._browse_cert)
        cert_row.addWidget(self.cert_path_input, 1)
        cert_row.addWidget(cert_browse)
        cert_layout.addLayout(cert_row)

        key_row = QHBoxLayout()
        key_row.addWidget(QLabel("Key file: "))
        self.cert_key_input = QLineEdit()
        self.cert_key_input.setPlaceholderText("Path to private key file (leave blank if combined)")
        key_browse = QPushButton("Browse…")
        key_browse.setFixedWidth(70)
        key_browse.clicked.connect(self._browse_cert_key)
        key_row.addWidget(self.cert_key_input, 1)
        key_row.addWidget(key_browse)
        cert_layout.addLayout(key_row)

        layout.addWidget(cert_group)
        return w

    def _browse_cert(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Certificate File", "",
            "Certificate files (*.pem *.crt *.cer);;All files (*)"
        )
        if path:
            self.cert_path_input.setText(path)

    def _browse_cert_key(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Private Key File", "",
            "Key files (*.pem *.key);;All files (*)"
        )
        if path:
            self.cert_key_input.setText(path)

    # ── cURL import ───────────────────────────────────────────────────

    def _import_from_curl(self) -> None:
        """Open a dialog to paste a cURL command and populate the editor."""
        from PyQt6.QtWidgets import QInputDialog, QApplication
        from equinox.core.curl_parser import parse_curl

        # Pre-fill with clipboard contents if it looks like a curl command
        clipboard_text = QApplication.clipboard().text().strip()
        prefill = clipboard_text if clipboard_text.lower().startswith("curl ") else ""

        text, ok = QInputDialog.getMultiLineText(
            self, "Import from cURL", "Paste a cURL command:", prefill
        )
        if not ok or not text.strip():
            return

        try:
            parsed = parse_curl(text.strip())
        except Exception as exc:
            QMessageBox.warning(self, "Parse Error", f"Could not parse cURL command:\n{exc}")
            return

        # Populate the editor
        method = parsed.get("method", "GET")
        idx = self.method_combo.findText(method)
        if idx >= 0:
            self.method_combo.setCurrentIndex(idx)
        self.url_input.setText(parsed.get("url", ""))
        self.headers_table.set_data(parsed.get("headers") or {})
        body = parsed.get("body")
        if body:
            self.body_text.setPlainText(body)
            self.body_type_combo.setCurrentText(
                self._detect_body_type(body, parsed.get("headers") or {})
            )
        else:
            self.body_text.clear()
            self.body_type_combo.setCurrentIndex(0)
        if not parsed.get("verify_ssl", True):
            self.verify_ssl_check.setChecked(False)
        self._mark_dirty()
        self._status_message("Request imported from cURL command")

    # ── Benchmark ─────────────────────────────────────────────────────

    def _open_benchmark(self) -> None:
        """Open the benchmark dialog for the currently configured request."""
        url = self.url_input.text().strip()
        if not url:
            QMessageBox.warning(self, "No Request", "Enter a URL before running a benchmark.")
            return
        method = self.method_combo.currentText()
        headers = self.headers_table.get_data()
        body_type = self.body_type_combo.currentText()
        body = None
        if body_type not in ("none", "multipart/form-data", "GraphQL"):
            body = self.body_text.toPlainText().strip() or None
        req = Request(
            method=method,
            url=url,
            headers=headers,
            body=body,
            timeout=self.timeout_spin.value(),
            verify_ssl=self.verify_ssl_check.isChecked(),
            follow_redirects=self.follow_redirects_check.isChecked(),
        )
        dlg = BenchmarkDialog(req, self, cookie_manager=self._cookie_manager)
        dlg.exec()

    # ── Pre-flight validation ─────────────────────────────────────────

    def _run_preflight_checks(self) -> list:
        """Return a list of advisory warning strings (empty = all clear)."""
        import re
        warnings = []
        url = self.url_input.text().strip()

        if url and "{{" not in url:
            if not re.match(r'^https?://', url, re.IGNORECASE):
                warnings.append("URL does not start with http:// or https://")

        # Check auth completeness
        from equinox.auth import BearerAuth, BasicAuth, APIKeyAuth, OAuth2Auth
        auth = self._auth or self._inherited_auth
        if auth is not None:
            if isinstance(auth, BearerAuth) and not getattr(auth, "token", None):
                warnings.append("Bearer token is empty")
            elif isinstance(auth, BasicAuth) and not getattr(auth, "username", None):
                warnings.append("Basic auth username is empty")
            elif isinstance(auth, APIKeyAuth) and not getattr(auth, "value", None):
                warnings.append("API key value is empty")
            elif isinstance(auth, OAuth2Auth) and not getattr(auth, "token_url", None):
                warnings.append("OAuth2 token URL is not configured")

        return warnings

    # ── Headers bulk actions and presets ─────────────────────────────

    def _headers_set_all(self, enabled: bool) -> None:
        """Enable or disable every row in the headers table."""
        for row in range(self.headers_table.rowCount()):
            chk_item = self.headers_table.item(row, 0)
            if chk_item is not None:
                chk_item.setCheckState(
                    Qt.CheckState.Checked if enabled else Qt.CheckState.Unchecked
                )

    def _params_set_all(self, enabled: bool) -> None:
        """Enable or disable every row in the params table."""
        for row in range(self.params_table.rowCount()):
            chk_item = self.params_table.item(row, 0)
            if chk_item is not None:
                chk_item.setCheckState(
                    Qt.CheckState.Checked if enabled else Qt.CheckState.Unchecked
                )

    def _insert_header_preset(self, key: str, value: str) -> None:
        """Append a header preset row (or update existing key)."""
        existing = self.headers_table.get_all_rows()
        for row_data in existing:
            if row_data.get("key", "").lower() == key.lower():
                # Key already present — select the row so user can edit the value
                return
        self.headers_table.add_row(key, value)

    # ── Captures ──────────────────────────────────────────────────────

    def _captures_add_row(self) -> None:
        r = self.captures_table.rowCount()
        self.captures_table.insertRow(r)
        self.captures_table.setItem(r, 0, QTableWidgetItem(""))
        source_combo = QComboBox()
        source_combo.addItems(["json", "header", "regex", "status"])
        self.captures_table.setCellWidget(r, 1, source_combo)
        self.captures_table.setItem(r, 2, QTableWidgetItem(""))
        self.captures_table.setItem(r, 3, QTableWidgetItem(""))

    def _captures_remove_row(self) -> None:
        rows = sorted(
            {idx.row() for idx in self.captures_table.selectedIndexes()},
            reverse=True,
        )
        for r in rows:
            self.captures_table.removeRow(r)

    def _get_captures(self) -> list:
        captures = []
        for r in range(self.captures_table.rowCount()):
            var_item = self.captures_table.item(r, 0)
            variable = var_item.text().strip() if var_item else ""
            if not variable:
                continue
            source_widget = self.captures_table.cellWidget(r, 1)
            source = source_widget.currentText() if source_widget else "json"
            path_item = self.captures_table.item(r, 2)
            path = path_item.text().strip() if path_item else ""
            default_item = self.captures_table.item(r, 3)
            default = default_item.text().strip() if default_item else ""
            captures.append({"variable": variable, "source": source, "path": path, "default": default})
        return captures

    def _set_captures(self, captures: list) -> None:
        self.captures_table.setRowCount(0)
        for cap in captures:
            if not isinstance(cap, dict):
                continue
            r = self.captures_table.rowCount()
            self.captures_table.insertRow(r)
            self.captures_table.setItem(r, 0, QTableWidgetItem(cap.get("variable", "")))
            source_combo = QComboBox()
            source_combo.addItems(["json", "header", "regex", "status"])
            src = cap.get("source", "json")
            idx = source_combo.findText(src)
            if idx >= 0:
                source_combo.setCurrentIndex(idx)
            self.captures_table.setCellWidget(r, 1, source_combo)
            self.captures_table.setItem(r, 2, QTableWidgetItem(cap.get("path", "")))
            self.captures_table.setItem(r, 3, QTableWidgetItem(cap.get("default", "")))

    # ── Send / Cancel ─────────────────────────────────────────────────

    def _send_request(self) -> None:
        from equinox.core.interpolation import VariableInterpolator
        from equinox.storage import EnvironmentManager

        url = self.url_input.text().strip()
        if not url:
            QMessageBox.warning(self, "Missing URL", "Please enter a request URL.")
            return

        # Pre-flight advisory warnings (non-blocking)
        pf_warnings = self._run_preflight_checks()
        if pf_warnings:
            self._preflight_label.setText("  ·  ".join(pf_warnings))
            self._preflight_banner.setVisible(True)
        else:
            self._preflight_banner.setVisible(False)

        # Don't start a second request while one is in flight
        if self._worker is not None and self._worker.isRunning():
            return

        method  = self.method_combo.currentText()
        headers = self.headers_table.get_data()
        params  = self.params_table.get_enabled_data()   # only checked rows are sent
        params_list = self.params_table.get_all_rows()   # full list incl. disabled
        body_type = self.body_type_combo.currentText()
        body    = None
        multipart_data = None

        if body_type == "multipart/form-data":
            multipart_data = self._get_multipart_data()
        elif body_type == "GraphQL":
            import json as _gjson
            _gql_q = self._gql_query.toPlainText().strip()
            _gql_v = self._gql_vars.toPlainText().strip()
            _gql_body: dict = {"query": _gql_q}
            try:
                _gql_parsed = _gjson.loads(_gql_v) if _gql_v else None
                if _gql_parsed is not None:
                    _gql_body["variables"] = _gql_parsed
            except Exception:
                pass
            body = _gjson.dumps(_gql_body)
        else:
            body = self.body_text.toPlainText().strip() or None

        # Auto Content-Type when not manually set
        if body and "Content-Type" not in headers:
            ct_map = {
                "raw (JSON)":      "application/json",
                "raw (XML)":       "application/xml",
                "form-urlencoded": "application/x-www-form-urlencoded",
                "GraphQL":         "application/json",
            }
            ct = ct_map.get(body_type)
            if ct:
                headers["Content-Type"] = ct
        # Note: do NOT set Content-Type for multipart — httpx sets it with boundary

        # Variable interpolation
        variables: Dict[str, str] = {}
        try:
            env_mgr = EnvironmentManager(self.db)
            active = env_mgr.get_active_environment()
            if active:
                variables.update(active.get("variables", {}))
        except Exception:
            pass

        # Include inherited collection variables (groups + collection-specific)
        # These override environment variables but are overridden by OS env / session.
        if self.current_request and self.current_request.collection_id:
            try:
                from equinox.storage import CollectionManager
                col_mgr = CollectionManager(self.db)
                col_vars = col_mgr.get_all_collection_variables(
                    self.current_request.collection_id
                )
                variables.update(col_vars)
            except Exception:
                pass

        variables.update({k: v for k, v in os.environ.items() if isinstance(v, str)})
        variables.update(self._session_vars)  # captured session vars override env

        # ── Pre-request script ────────────────────────────────────────
        pre_src = self.pre_script_editor.toPlainText()
        if pre_src.strip():
            try:
                from equinox.core.scripts import ScriptRunner
                req_dict = {"method": method, "url": url,
                            "headers": dict(headers), "params": dict(params), "body": body}
                result = ScriptRunner.run_pre(pre_src, req_dict, self._session_vars)
                if result.error:
                    self.pre_script_result.setText(f"Error: {result.error}")
                    self.pre_script_result.setStyleSheet(f"color: {Colors.RED};")
                else:
                    self._session_vars.update(result.output_vars)
                    variables.update(self._session_vars)  # re-inject after script
                    msg = f"OK — {len(result.output_vars)} var(s) set" if result.output_vars else "OK"
                    self.pre_script_result.setText(msg)
                    self.pre_script_result.setStyleSheet(f"color: {Colors.GREEN};")
            except Exception as exc:
                logger.debug("Pre-script failed: %s", exc)

        try:
            url = VariableInterpolator.interpolate(url, variables)
            headers = {
                VariableInterpolator.interpolate(k, variables):
                VariableInterpolator.interpolate(v, variables)
                for k, v in headers.items()
            }
            params = {
                VariableInterpolator.interpolate(k, variables):
                VariableInterpolator.interpolate(v, variables)
                for k, v in params.items()
            }
            if body:
                body = VariableInterpolator.interpolate(body, variables)
        except Exception as exc:
            QMessageBox.warning(self, "Variable Error",
                                f"Failed to expand variables:\n{exc}")
            return

        cert_path = self.cert_path_input.text().strip() or None
        cert_key  = self.cert_key_input.text().strip() or None

        # Resolve effective auth: own > inherited (folder > collection)
        # Re-resolve from DB at send time so tokens are always fresh.
        effective_auth = self._auth
        inherited_source = None
        if effective_auth is None and self.current_request and self.current_request.collection_id:
            try:
                from equinox.storage import CollectionManager
                collection_manager = CollectionManager(self.db)
                # Build a lightweight probe with no auth so that
                # resolve_effective_auth walks the full hierarchy
                # (folder → collection) instead of short-circuiting
                # on a previously-resolved auth baked into current_request.
                probe = Request(
                    method="GET", url="",
                    collection_id=self.current_request.collection_id,
                    folder=self.current_request.folder,
                )
                inh, inherited_source = collection_manager.resolve_effective_auth(probe)
                if inh is not None:
                    effective_auth = inh
            except Exception as exc:
                logger.debug("Send-time inherited auth resolution failed: %s", exc)
        # Fallback to cached inherited auth if DB resolution failed
        if effective_auth is None and getattr(self, "_inherited_auth", None):
            effective_auth = self._inherited_auth
            inherited_source = getattr(self, "_inherited_auth_source", None)

        # Track for post-send save-back of refreshed tokens
        self._send_inherited_auth = effective_auth if self._auth is None else None
        self._send_inherited_source = inherited_source if self._auth is None else None

        # Carry forward collection context from the loaded request so that
        # inherited auth, collection variables, and autosave keep working
        # even after the first send replaces self.current_request.
        _prev = self.current_request
        request = Request(
            method=method, url=url, headers=headers,
            params=params, params_list=params_list,
            body=body, auth=effective_auth,
            timeout=self.timeout_spin.value(),
            verify_ssl=self.verify_ssl_check.isChecked(),
            follow_redirects=self.follow_redirects_check.isChecked(),
            captures=self._get_captures(),
            pre_script=self.pre_script_editor.toPlainText(),
            post_script=self.post_script_editor.toPlainText(),
            cert_path=cert_path,
            cert_key_path=cert_key,
            multipart_data=multipart_data,
            collection_id=getattr(_prev, "collection_id", None),
            folder=getattr(_prev, "folder", None),
            id=getattr(_prev, "id", None),
            name=getattr(_prev, "name", None),
            path_params=self.path_params_table.get_all_data(),
        )
        self.current_request = request

        logger.info(
            "Sending %s %s", method, url,
            extra={"method": method, "url": url},
        )
        log_panel = self._logging_panel
        if log_panel:
            log_panel.log_request(request)

        self.request_sent.emit(request)
        self._set_sending_state(True)
        self._clear_dirty()

        self._worker = RequestWorker(request, self, cookie_manager=self._cookie_manager)
        worker_ref = self._worker
        self._worker.finished.connect(
            lambda result, w=worker_ref: self._handle_response(result, w)
        )
        self._worker.start()

    def _cancel_request(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self._worker.quit()
            self._worker = None
        self._set_sending_state(False)
        self._status_message("Request cancelled", 4000)

    # ── Response handling ─────────────────────────────────────────────

    def _handle_response(self, result: object, worker: RequestWorker) -> None:
        # Stale result guard: only applies when _worker is still set
        if self._worker is not None and worker is not self._worker:
            return  # Stale result from a cancelled/replaced worker
        self._worker = None
        self._set_sending_state(False)

        if isinstance(result, RichError):
            logger.error(
                "Request failed: %s", result.message,
                extra={"error_type": result.exc_type,
                       "url": getattr(self.current_request, "url", ""),
                       "method": getattr(self.current_request, "method", "")},
            )
            self._status_message(f"Error: {result.message}", 8000)
            # Rich dialog: show type + message + hint about log file
            from equinox.core.log_setup import get_log_file
            log_hint = f"\n\nFull details in: {get_log_file()}" if get_log_file() else ""
            QMessageBox.critical(
                self, f"Request Failed — {result.exc_type}",
                f"{result.message}{log_hint}",
            )
            log_panel = self._logging_panel
            if log_panel:
                log_panel.log_error(self.current_request, result.message)
            _save_history_safe(self.db, self.current_request, error=result.message)
            self._persist_inherited_auth_tokens()

        elif isinstance(result, Exception):
            # Fallback for any exception that slipped through un-enriched
            rich = enrich_exception(result)
            self._handle_response(rich, worker)  # recurse once

        else:
            response: Response = result
            elapsed_ms = int(response.elapsed * 1000)
            logger.info(
                "%s %s → %d %s (%d ms)",
                response.request.method, response.request.url,
                response.status_code, response.reason, elapsed_ms,
                extra={
                    "method": response.request.method,
                    "url": response.request.url,
                    "status": response.status_code,
                    "elapsed_ms": elapsed_ms,
                    "size_bytes": response.size,
                },
            )
            self._status_message(
                f"{response.status_code} {response.reason}  —  {elapsed_ms} ms", 8000
            )
            self.response_received.emit(response)
            self._apply_captures(response)
            self._evaluate_assertions(response)
            self._run_post_script(response)
            self._refresh_url_completer()
            log_panel = self._logging_panel
            if log_panel:
                log_panel.log_response(self.current_request, response)
            _save_history_safe(self.db, self.current_request, response)

            # Save refreshed inherited-auth tokens back to collection/folder
            self._persist_inherited_auth_tokens()

    def _apply_captures(self, response: Response) -> None:
        """Run capture rules against the response and update session vars."""
        try:
            from equinox.core.captures import CaptureEngine
            caps_raw = getattr(response.request, "captures", [])
            if not caps_raw:
                return
            results = CaptureEngine.apply_all(
                CaptureEngine.from_dict_list(caps_raw), response
            )
            for r in results:
                self._session_vars[r.variable] = r.value
            lines = [
                f"{'✓' if r.success else '✗'} {r.variable} = {r.value!r}"
                + (f"  ({r.error})" if not r.success else "")
                for r in results
            ]
            self.captures_results_label.setText("\n".join(lines) if lines else "—")
        except Exception as exc:
            logger.debug("Capture processing failed: %s", exc, exc_info=True)

    def _run_post_script(self, response: Response) -> None:
        """Execute the post-response script if one is defined."""
        post_src = self.post_script_editor.toPlainText()
        if not post_src.strip():
            return
        try:
            from equinox.core.scripts import ScriptRunner
            resp_dict: Dict = {
                "status_code": response.status_code,
                "headers": dict(response.headers),
                "body": response.text if hasattr(response, "text") else "",
                "json": None,
            }
            try:
                resp_dict["json"] = response.json()
            except Exception:
                pass
            script_result = ScriptRunner.run_post(
                post_src, resp_dict, self._session_vars
            )
            if script_result.error:
                self.post_script_result.setText(f"Error: {script_result.error}")
                self.post_script_result.setStyleSheet(f"color: {Colors.RED};")
            else:
                self._session_vars.update(script_result.output_vars)
                msg = (
                    f"OK — {len(script_result.output_vars)} var(s) set"
                    if script_result.output_vars else "OK"
                )
                self.post_script_result.setText(msg)
                self.post_script_result.setStyleSheet(f"color: {Colors.GREEN};")
        except Exception as exc:
            logger.debug("Post-script failed: %s", exc)


    # ── Elapsed timer ─────────────────────────────────────────────────

    def _persist_inherited_auth_tokens(self) -> None:
        """Save back refreshed tokens on inherited auth to the DB.

        After ``OAuth2Auth.apply()`` auto-refreshes a token, the in-memory
        object has the new ``access_token`` and ``expires_at``.  This method
        writes them back to the collection or folder so subsequent requests
        reuse the token instead of fetching a new one every time.
        """
        auth = getattr(self, "_send_inherited_auth", None)
        source = getattr(self, "_send_inherited_source", None)
        if auth is None or source is None:
            return
        from equinox.auth import OAuth2Auth
        if not isinstance(auth, OAuth2Auth):
            return
        # Only write back if the object now has a token (i.e. apply() ran)
        if not auth.access_token:
            return
        try:
            from equinox.storage import CollectionManager
            mgr = CollectionManager(self.db)
            req = self.current_request
            if not req or not req.collection_id:
                return
            if source == "collection":
                mgr.set_collection_auth(req.collection_id, auth)
            elif source.startswith("folder:"):
                folder_path = source[7:]
                mgr.set_folder_auth(req.collection_id, folder_path, auth)
            # Update display to show the fresh token info
            self._inherited_auth = auth
            self._inherited_auth_source = source
            self._update_auth_display(self._auth)
        except Exception as exc:
            logger.debug("Failed to persist inherited auth tokens: %s", exc)

    def _set_sending_state(self, sending: bool) -> None:
        if sending:
            self._elapsed_secs = 0.0
            self._elapsed_timer.start()
            self.send_button.setEnabled(False)
            self.send_button.setText("0.0s…")
            self.cancel_button.setVisible(True)
            self.url_input.setEnabled(False)
            self.method_combo.setEnabled(False)
        else:
            self._elapsed_timer.stop()
            self.send_button.setEnabled(True)
            self.send_button.setText("Send")
            self.cancel_button.setVisible(False)
            self.url_input.setEnabled(True)
            self.method_combo.setEnabled(True)

    def _tick_elapsed(self) -> None:
        self._elapsed_secs += 0.1
        self.send_button.setText(f"{self._elapsed_secs:.1f}s…")

    # ── Auth ──────────────────────────────────────────────────────────

    def _configure_auth(self) -> None:
        from equinox.gui.auth_dialog import AuthDialog
        # Show inherited auth in the dialog so the user sees what's active
        display_auth = self._auth or self._inherited_auth
        dialog = AuthDialog(display_auth, self, db=self.db)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            if hasattr(dialog, '_saved_auth'):
                self._auth = dialog._saved_auth
                if self._auth is not None:
                    # Own auth supersedes inherited
                    self._inherited_auth = None
                    self._inherited_auth_source = None
                else:
                    # User chose "No Auth" — re-resolve inherited
                    self._resolve_inherited_auth()
                self._update_auth_display(self._auth)

    def _clear_auth(self) -> None:
        self._auth = None
        self._resolve_inherited_auth()
        self._update_auth_display(None)

    def _resolve_inherited_auth(self) -> None:
        """Re-resolve inherited auth from the collection/folder hierarchy.

        Called after clearing own auth, after the auth dialog sets "No Auth",
        and when the collection's auth configuration changes externally.
        """
        self._inherited_auth = None
        self._inherited_auth_source = None
        if self.current_request and getattr(self.current_request, "collection_id", None):
            try:
                from equinox.storage import CollectionManager
                mgr = CollectionManager(self.db)
                probe = Request(
                    method="GET", url="",
                    collection_id=self.current_request.collection_id,
                    folder=getattr(self.current_request, "folder", None),
                )
                inh_auth, inh_source = mgr.resolve_effective_auth(probe)
                if inh_auth is not None:
                    self._inherited_auth = inh_auth
                    self._inherited_auth_source = inh_source
            except Exception as exc:
                logger.debug("Failed to resolve inherited auth: %s", exc)

    def refresh_inherited_auth(self) -> None:
        """Public method for external callers (e.g. window signal wiring)
        to trigger an inherited-auth refresh and update the display."""
        if self._auth is None:
            self._resolve_inherited_auth()
            self._update_auth_display(self._auth)

    def _update_auth_display(self, auth=None) -> None:
        from equinox.auth import BasicAuth, OAuth2Auth, BearerAuth, APIKeyAuth
        self.auth_status_label.setText("")
        self.auth_status_label.setStyleSheet("")

        # If no own auth, check inherited
        display_auth = auth
        inherited_label = ""
        if not display_auth and getattr(self, "_inherited_auth", None):
            display_auth = self._inherited_auth
            source = getattr(self, "_inherited_auth_source", "") or ""
            if source.startswith("folder:"):
                inherited_label = f"  (inherited from folder \"{source[7:]}\")"
            elif source == "collection":
                inherited_label = "  (inherited from collection)"

        if not display_auth:
            self.auth_type_label.setText("Auth: None")
            self.auth_details_label.setText("No authentication configured")
        elif isinstance(display_auth, BasicAuth):
            self.auth_type_label.setText(f"Auth: Basic{inherited_label}")
            self.auth_details_label.setText(f"Username: {display_auth.username}")
        elif isinstance(display_auth, BearerAuth):
            preview = display_auth.token[:8] + "…" if len(display_auth.token) > 8 else "***"
            self.auth_type_label.setText(f"Auth: Bearer Token{inherited_label}")
            self.auth_details_label.setText(f"Token: {preview}")
        elif isinstance(display_auth, OAuth2Auth):
            from datetime import datetime, timezone
            self.auth_type_label.setText(f"Auth: OAuth 2.0{inherited_label}")
            self.auth_details_label.setText(
                f"Token URL: {display_auth.token_url or '—'}\nClient ID: {display_auth.client_id or '—'}"
            )
            info = display_auth.get_token_info()
            if not display_auth.access_token:
                text, color = "Token: None", Colors.RED
            elif info["needs_refresh"]:
                text, color = f"Token: Expiring soon  [{info['access_token']}]", Colors.AMBER
            else:
                text, color = f"Token: Valid  [{info['access_token']}]", Colors.GREEN
            if info["expires_at"]:
                try:
                    secs = int((datetime.fromisoformat(info["expires_at"]) -
                                datetime.now(timezone.utc).replace(tzinfo=None)).total_seconds())
                    text += f"  (expires in {secs}s)" if secs > 0 else "  (expired)"
                except Exception:
                    pass
            self.auth_status_label.setText(text)
            self.auth_status_label.setStyleSheet(f"color: {color};")
        elif isinstance(display_auth, APIKeyAuth):
            preview = display_auth.value[:4] + "…" if len(display_auth.value) > 4 else "***"
            self.auth_type_label.setText(f"Auth: API Key{inherited_label}")
            self.auth_details_label.setText(f"{display_auth.key} = {preview}  ({display_auth.location})")
        else:
            # Unknown auth type (e.g. AWS SigV4)
            type_name = type(display_auth).__name__
            self.auth_type_label.setText(f"Auth: {type_name}{inherited_label}")
            self.auth_details_label.setText("")

    # ── Body type ─────────────────────────────────────────────────────

    def _on_body_type_changed(self, _index: int) -> None:
        sel = self.body_type_combo.currentText()
        is_multipart = sel == "multipart/form-data"
        is_json = sel == "raw (JSON)"
        is_gql  = sel == "GraphQL"

        # Show/hide the multipart table vs. raw text editor vs. GraphQL editor
        self.body_text.setVisible(not is_multipart and not is_gql)
        self._multipart_table.setVisible(is_multipart)
        self._mp_btns_widget.setVisible(is_multipart)
        self._fmt_json_btn.setVisible(is_json)
        self._gql_widget.setVisible(is_gql)

        if sel == "none":
            self.body_text.setEnabled(False)
            self.body_text.setPlaceholderText("(no body)")
        elif not is_multipart and not is_gql:
            self.body_text.setEnabled(True)
            ph = {
                "raw (JSON)":      '{\n  "key": "value"\n}',
                "raw (XML)":       "<root>\n  <item>value</item>\n</root>",
                "raw (text)":      "Plain text body",
                "form-urlencoded": "key1=value1&key2=value2",
            }
            self.body_text.setPlaceholderText(ph.get(sel, ""))

        self._update_tab_labels()

    # ── Tab count badges (#5) ─────────────────────────────────────────

    def _update_tab_labels(self, *_args) -> None:
        """Update tab labels to show data counts as badges."""
        try:
            h = len(self.headers_table.get_data())
            # Show total param count (enabled + disabled) so users see all saved params
            p = len(self.params_table.get_all_rows())
            self.tabs.setTabText(0, f"Headers ({h})" if h else "Headers")
            self.tabs.setTabText(1, f"Params ({p})" if p else "Params")
            bt = self.body_type_combo.currentText()
            if bt == "multipart/form-data":
                mp = len(self._get_multipart_data())
                self.tabs.setTabText(2, f"Body ({mp})" if mp else "Body")
            elif bt != "none" and self.body_text.toPlainText().strip():
                self.tabs.setTabText(2, "Body ●")
            else:
                self.tabs.setTabText(2, "Body")
        except Exception:
            pass

    # ── URL ghost-params preview ──────────────────────────────────────

    def _update_url_suffix(self, *_) -> None:
        """Repaint the URL bar with the current enabled params as a ghost suffix."""
        try:
            enabled = self.params_table.get_enabled_data()
            if not enabled:
                self.url_input.set_param_suffix("")
                return
            sep = "&" if "?" in self.url_input.text() else "?"
            parts = [f"{k}={v}" for k, v in enabled.items() if k]
            self.url_input.set_param_suffix(sep + "&".join(parts))
        except Exception:
            self.url_input.set_param_suffix("")

    # ── Format JSON (#6) ──────────────────────────────────────────────

    def _format_json_body(self) -> None:
        """Pretty-print the JSON in the body editor."""
        import json as _json
        text = self.body_text.toPlainText()
        try:
            parsed = _json.loads(text)
            self.body_text.setPlainText(_json.dumps(parsed, indent=2, ensure_ascii=False))
        except _json.JSONDecodeError as exc:
            self._status_message(f"Invalid JSON: {exc}", 5000)

    # ── Multipart form-data (#7) ──────────────────────────────────────

    def _multipart_add_row(self) -> None:
        r = self._multipart_table.rowCount()
        self._multipart_table.insertRow(r)
        self._multipart_table.setItem(r, 0, QTableWidgetItem(""))
        type_combo = QComboBox()
        type_combo.addItems(["text", "file"])
        self._multipart_table.setCellWidget(r, 1, type_combo)
        self._multipart_table.setItem(r, 2, QTableWidgetItem(""))
        self._multipart_table.setCurrentCell(r, 0)
        self._multipart_table.editItem(self._multipart_table.item(r, 0))
        self._dirty = True
        self._update_tab_labels()

    def _multipart_remove_row(self) -> None:
        rows = sorted(
            {i.row() for i in self._multipart_table.selectedItems()}, reverse=True
        )
        for r in rows:
            self._multipart_table.removeRow(r)
        self._dirty = True
        self._update_tab_labels()

    def _multipart_browse_file(self) -> None:
        row = self._multipart_table.currentRow()
        if row < 0:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Select File", "", "All Files (*)")
        if not path:
            return
        # Set type to "file"
        type_widget = self._multipart_table.cellWidget(row, 1)
        if type_widget:
            type_widget.setCurrentText("file")
        self._multipart_table.setItem(row, 2, QTableWidgetItem(path))
        self._dirty = True

    def _get_multipart_data(self) -> list:
        fields = []
        for r in range(self._multipart_table.rowCount()):
            key_item = self._multipart_table.item(r, 0)
            key = key_item.text().strip() if key_item else ""
            if not key:
                continue
            type_widget = self._multipart_table.cellWidget(r, 1)
            field_type = type_widget.currentText() if type_widget else "text"
            val_item = self._multipart_table.item(r, 2)
            value = val_item.text() if val_item else ""
            fields.append({"key": key, "type": field_type, "value": value})
        return fields

    def _set_multipart_data(self, fields: list) -> None:
        self._multipart_table.setRowCount(0)
        for field in fields:
            r = self._multipart_table.rowCount()
            self._multipart_table.insertRow(r)
            self._multipart_table.setItem(r, 0, QTableWidgetItem(field.get("key", "")))
            type_combo = QComboBox()
            type_combo.addItems(["text", "file"])
            ft = field.get("type", "text")
            type_combo.setCurrentText(ft if ft in ("text", "file") else "text")
            self._multipart_table.setCellWidget(r, 1, type_combo)
            self._multipart_table.setItem(r, 2, QTableWidgetItem(field.get("value", "")))

    # ── Save / Load / Clear ───────────────────────────────────────────

    def _save_request(self) -> None:
        from equinox.storage import CollectionManager

        url = self.url_input.text().strip()
        if not url:
            QMessageBox.warning(self, "Missing URL", "Please enter a URL before saving.")
            return

        method  = self.method_combo.currentText()
        headers = self.headers_table.get_data()
        params  = self.params_table.get_enabled_data()
        params_list = self.params_table.get_all_rows()
        body    = self.body_text.toPlainText().strip() or None

        dialog = QDialog(self)
        dialog.setWindowTitle("Save Request")
        dialog.setMinimumWidth(420)
        dlg_layout = QVBoxLayout(dialog)

        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Name:"))
        name_input = QLineEdit()
        name_input.setPlaceholderText(f"{method} {url[:50]}")
        name_row.addWidget(name_input)
        dlg_layout.addLayout(name_row)

        col_row = QHBoxLayout()
        col_row.addWidget(QLabel("Collection:"))
        col_combo = QComboBox()
        mgr = CollectionManager(self.db)
        collections = mgr.list_collections()
        if not collections:
            try:
                mgr.create_collection("My Requests", "Default collection")
                collections = mgr.list_collections()
            except Exception as exc:
                QMessageBox.critical(self, "Error", f"Could not create collection: {exc}")
                return
        for col in collections:
            col_combo.addItem(col["name"], col["id"])
        col_row.addWidget(col_combo)
        dlg_layout.addLayout(col_row)

        folder_row = QHBoxLayout()
        folder_row.addWidget(QLabel("Folder:"))
        folder_input = QLineEdit()
        folder_input.setPlaceholderText("e.g. Auth/OAuth  (optional)")
        # Pre-fill from current request if available
        if self.current_request and getattr(self.current_request, "folder", None):
            folder_input.setText(self.current_request.folder)
        folder_row.addWidget(folder_input)
        dlg_layout.addLayout(folder_row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        dlg_layout.addWidget(buttons)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        name   = name_input.text().strip() or f"{method} {url[:50]}"
        col_id = col_combo.currentData()
        folder = folder_input.text().strip() or None

        request = Request(
            method=method, url=url, headers=headers,
            params=params, params_list=params_list,
            body=body, name=name, auth=self._auth,
            timeout=self.timeout_spin.value(),
            verify_ssl=self.verify_ssl_check.isChecked(),
            follow_redirects=self.follow_redirects_check.isChecked(),
            folder=folder,
            captures=self._get_captures(),
            assertions=self._get_assertions(),
            pre_script=self.pre_script_editor.toPlainText(),
            post_script=self.post_script_editor.toPlainText(),
            cert_path=self.cert_path_input.text().strip() or None,
            cert_key_path=self.cert_key_input.text().strip() or None,
            description=self.notes_editor.toPlainText().strip() or None,
            path_params=self.path_params_table.get_all_data(),
        )
        try:
            mgr.save_request(request, collection_id=col_id, name=name)
            self._status_message(f"Saved '{name}' to '{col_combo.currentText()}'")
            try:
                win = self.window()
                if hasattr(win, 'collections_panel'):
                    win.collections_panel.refresh()
            except Exception:
                pass
        except Exception as exc:
            QMessageBox.critical(self, "Save Failed", str(exc))

    def load_request(self, request: Request) -> None:
        self.url_input.setText(request.url)
        idx = self.method_combo.findText(request.method)
        if idx >= 0:
            self.method_combo.setCurrentIndex(idx)
        self.headers_table.set_data(request.headers or {})
        # Prefer the rich params_list (with enabled flags) when present
        pl = getattr(request, "params_list", None)
        self.params_table.set_data(pl if pl else (request.params or {}))
        mp_data = getattr(request, "multipart_data", None)
        if mp_data:
            self._set_multipart_data(mp_data)
            self.body_type_combo.setCurrentText("multipart/form-data")
            self.body_text.clear()
        elif request.body:
            self.body_text.setPlainText(request.body)
            self._multipart_table.setRowCount(0)
            # Auto-detect body type
            detected = self._detect_body_type(request.body, request.headers)
            self.body_type_combo.setCurrentText(detected)
        else:
            self.body_text.clear()
            self._multipart_table.setRowCount(0)
            self.body_type_combo.setCurrentText("none")
        self._auth = getattr(request, 'auth', None)
        self.current_request = request
        # Resolve inherited auth when request has no own auth.
        if self._auth is None:
            self._resolve_inherited_auth()
        else:
            self._inherited_auth = None
            self._inherited_auth_source = None
        self._update_auth_display(self._auth)
        self._set_captures(getattr(request, "captures", None) or [])
        self._set_assertions(getattr(request, "assertions", None) or [])
        self.pre_script_editor.setPlainText(getattr(request, "pre_script", "") or "")
        self.post_script_editor.setPlainText(getattr(request, "post_script", "") or "")
        self.cert_path_input.setText(getattr(request, "cert_path", "") or "")
        self.cert_key_input.setText(getattr(request, "cert_key_path", "") or "")
        self.timeout_spin.setValue(getattr(request, "timeout", DEFAULT_TIMEOUT) or DEFAULT_TIMEOUT)
        self.verify_ssl_check.setChecked(bool(getattr(request, "verify_ssl", True)))
        self.follow_redirects_check.setChecked(bool(getattr(request, "follow_redirects", True)))
        self.pre_script_result.setText("")
        self.post_script_result.setText("")
        self.notes_editor.setPlainText(getattr(request, "description", "") or "")
        # Restore path parameters: load saved values, then re-extract from URL
        self.path_params_table.set_data(getattr(request, "path_params", None) or {})
        self.path_params_table.update_from_url(request.url)
        self._path_params_widget.setVisible(self.path_params_table.rowCount() > 0)
        self._clear_dirty()
        self._update_tab_labels()
        self._update_url_suffix()

    @staticmethod
    def _detect_body_type(body: str, headers: Optional[Dict] = None) -> str:
        """Guess body type from content or Content-Type header (#7)."""
        import json as _json
        ct = (headers or {}).get("Content-Type", "").lower()
        if "json" in ct:
            return "raw (JSON)"
        if "xml" in ct:
            return "raw (XML)"
        if "urlencoded" in ct:
            return "form-urlencoded"
        if "text" in ct:
            return "raw (text)"
        # Sniff content
        stripped = body.strip()
        if stripped.startswith(("{", "[")):
            try:
                _json.loads(stripped)
                return "raw (JSON)"
            except Exception:
                pass
        if stripped.startswith("<") and (">" in stripped):
            return "raw (XML)"
        if "=" in stripped and "&" in stripped:
            return "form-urlencoded"
        return "raw (text)"

    def clear(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._cancel_request()
        self.url_input.clear()
        self.method_combo.setCurrentIndex(0)
        self.headers_table.reset()
        self.params_table.reset()
        self.path_params_table.reset()
        self._path_params_widget.setVisible(False)
        self.body_text.clear()
        self._multipart_table.setRowCount(0)
        self._gql_query.clear()
        self._gql_vars.clear()
        self.body_type_combo.setCurrentIndex(0)
        # Auth intentionally kept — user almost always wants to reuse it
        self.captures_table.setRowCount(0)
        self.captures_results_label.setText("—")
        self.assertions_table.setRowCount(0)
        self.assertions_results_label.setText("—")
        # Reset Assertions tab title
        for i in range(self.tabs.count()):
            if self.tabs.tabText(i).startswith("Assertions"):
                self.tabs.setTabText(i, "Assertions")
                break
        self.pre_script_editor.clear()
        self.post_script_editor.clear()
        self.pre_script_result.setText("")
        self.post_script_result.setText("")
        self.cert_path_input.clear()
        self.cert_key_input.clear()
        self.timeout_spin.setValue(DEFAULT_TIMEOUT)
        self.verify_ssl_check.setChecked(True)
        self.follow_redirects_check.setChecked(True)
        self.notes_editor.clear()
        # _session_vars intentionally kept — persists for request chaining
        self.current_request = None
        self._clear_dirty()
        self._update_tab_labels()
        self._update_url_suffix()


# ─────────────────────────────────────────────────────────────────────────────
# Backward-compat aliases — use core.error_enrichment directly in new code
# ─────────────────────────────────────────────────────────────────────────────

_RichError = RichError
_enrich_exception = enrich_exception


def _save_history_safe(db: Database, request, response=None, error=None) -> None:
    """Save to history without letting exceptions bubble to the UI."""
    if request is None:
        return
    try:
        mgr = HistoryManager(db)
        if response is not None:
            mgr.save_history(request, response)
        elif error is not None:
            mgr.save_history(request, error=error)
    except Exception:
        logger.debug("Failed to save history", exc_info=True)



