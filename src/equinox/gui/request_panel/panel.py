"""Request builder panel."""

import logging
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
    QHeaderView,
    QLabel,
    QGroupBox,
    QMessageBox,
    QDialog,
    QCompleter,
    QSplitter,
    QFileDialog,
    QCheckBox,
    QDoubleSpinBox,
    QFormLayout, QToolButton,
)
from PyQt6.QtCore import pyqtSignal, Qt, QTimer, QStringListModel
from PyQt6.QtGui import QKeySequence, QShortcut

from equinox.gui.request_panel.body_text_proxy import BodyTextProxy
from equinox.gui.theme import Colors, get_mono_font
from equinox.gui.widgets import UrlLineEdit, CheckableKeyValueTable, JsonBodyEditor, PathParamsTable
from equinox.core.request import Request
from equinox.core.error_enrichment import RichError, enrich_exception
from equinox.storage import Database, HistoryManager
from equinox.gui.workers import RequestWorker, BenchmarkDialog, DEFAULT_TIMEOUT  # noqa: F401
from equinox.gui.request_panel.mixins import (  # noqa: F401
    _RequestSendMixin,
    _RequestAuthMixin,
    _save_history_safe,
)
from equinox.gui.request_panel.body_mixin import _RequestBodyMixin  # noqa: F401
from equinox.gui.request_panel.save_dialog import SaveRequestDialog  # noqa: F401
from equinox.gui.request_panel.toolbar import TabToolbar

logger = logging.getLogger(__name__)

# Common header presets for the Headers tab toolbar
_HEADER_PRESETS = [
    ("Content-Type: application/json",        "Content-Type", "application/json"),
    ("Content-Type: application/xml",         "Content-Type", "application/xml"),
    ("Content-Type: application/x-www-form-urlencoded",
     "Content-Type", "application/x-www-form-urlencoded"),
    ("Content-Type: multipart/form-data",     "Content-Type", "multipart/form-data"),
    ("Content-Type: text/plain",              "Content-Type", "text/plain"),
    None,
    ("Accept: application/json",              "Accept", "application/json"),
    ("Accept: application/xml",               "Accept", "application/xml"),
    ("Accept: */*",                           "Accept", "*/*"),
    None,
    ("Authorization: Bearer …",               "Authorization", "Bearer "),
    ("X-API-Key: …",                          "X-API-Key", ""),
    None,
    ("Cache-Control: no-cache",               "Cache-Control", "no-cache"),
    ("User-Agent: Equinox/1.0",               "User-Agent", "Equinox/1.0"),
]

# Backward-compat alias
_SaveRequestDialog = SaveRequestDialog


# ─────────────────────────────────────────────────────────────────────────────
# Request panel
# ─────────────────────────────────────────────────────────────────────────────

class RequestPanel(_RequestSendMixin, _RequestAuthMixin, _RequestBodyMixin, QWidget):
    """Panel for building and sending HTTP requests."""

    response_received = pyqtSignal(object)
    request_sent      = pyqtSignal(object)
    session_vars_changed = pyqtSignal(dict)

    # ── Accessor helpers ───────────────────────────────────────────────

    @property
    def _logging_panel(self):
        """Return the main window's LoggingPanel, or None if unavailable."""
        try:
            win = self.window()
            return getattr(win, "logging_panel", None)
        except Exception:
            logger.debug("Could not access logging panel", exc_info=True)
            return None

    def _status_message(self, text: str, timeout_ms: int = 5000) -> None:
        """Show a message in the main window status bar (best-effort)."""
        try:
            self.window().statusBar().showMessage(text, timeout_ms)
        except Exception:
            logger.debug("Could not show status message: %s", text)

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

    # ── Autosave ──────────────────────────────────────────────────────

    def autosave_current(self) -> None:
        """Persist the current editor state back to the DB if dirty.

        Only acts when the loaded request originated from a collection (has an
        ``id``).  Silently does nothing for ad-hoc / history requests.
        """
        if not self._dirty:
            return
        req = self.current_request
        if not req or not getattr(req, "id", None):
            return
        try:
            from equinox.storage import CollectionManager
            mgr = CollectionManager(self.db)
            updated = Request(
                method=self.method_combo.currentText(),
                url=self.url_input.text().strip(),
                headers=self.headers_table.get_data(),
                params=self.params_table.get_enabled_data(),
                params_list=self.params_table.get_all_rows(),
                body=self.body_text.toPlainText().strip() or None,
                auth=self._auth,
                timeout=self.timeout_spin.value(),
                verify_ssl=self.verify_ssl_check.isChecked(),
                follow_redirects=self.follow_redirects_check.isChecked(),
                name=req.name,
                description=self.notes_editor.toPlainText().strip() or None,
                collection_id=req.collection_id,
                folder=req.folder,
                id=req.id,
                captures=self._get_captures(),
                assertions=self._get_assertions(),
                pre_script=self.pre_script_editor.toPlainText(),
                post_script=self.post_script_editor.toPlainText(),
                cert_path=self.cert_path_input.text().strip() or None,
                cert_key_path=self.cert_key_input.text().strip() or None,
                path_params=self.path_params_table.get_all_data(),
            )
            mgr.update_request(updated)
            self._dirty = False
            logger.debug("Autosaved request %d", req.id)
        except Exception:
            logger.debug("Autosave failed", exc_info=True)

    # ── Session variable accessors ─────────────────────────────────────

    def get_session_vars(self) -> Dict[str, str]:
        """Return a copy of the current session variables."""
        return dict(self._session_vars)

    def clear_session_vars(self) -> None:
        """Clear all session variables and notify listeners."""
        self._session_vars.clear()
        self.session_vars_changed.emit({})

    def _clear_dirty(self):
        self._dirty = False

    def _setup_dirty_tracking(self):
        """Connect change signals on all editor widgets to mark dirty."""
        def safe_connect(get_signal, slot, name=None):
            """Lazily retrieve a signal via get_signal() and connect it to slot.

            get_signal is a callable that returns the signal object when invoked.
            This avoids attribute access at function call time which can raise
            if the underlying C++ object was already deleted. Expected errors
            (AttributeError, RuntimeError) are common in headless/test envs
            and are logged at DEBUG to avoid noisy warning spam; unexpected
            exceptions are still surfaced as warnings.
            """
            try:
                sig = get_signal()
            except Exception as exc:
                # Common case: underlying C++ object removed -> AttributeError / RuntimeError
                if isinstance(exc, (AttributeError, RuntimeError)):
                    logger.debug("Could not retrieve signal %s: underlying object missing (%s)", name or str(get_signal), exc)
                else:
                    logger.warning("Could not retrieve signal %s: unexpected error", name or str(get_signal), exc_info=True)
                return
            try:
                sig.connect(slot)
            except RuntimeError as exc:
                # Underlying C++ object deleted after signal retrieval
                logger.debug("Failed to connect signal %s -> %s: underlying C++ object deleted (%s)",
                             name or repr(sig), slot, exc)

        safe_connect(lambda: self.url_input.textChanged, self._mark_dirty, "url_input.textChanged")
        safe_connect(lambda: self.method_combo.currentIndexChanged, self._mark_dirty, "method_combo.currentIndexChanged")
        # body_text is a wrapped C++ Qt object; attempt to connect but don't
        # let a RuntimeError abort initialization.
        safe_connect(lambda: getattr(self, "body_text").textChanged, self._mark_dirty, "body_text.textChanged")
        safe_connect(lambda: getattr(self, "body_text").textChanged, self._update_tab_labels, "body_text.textChanged->update_tab_labels")
        safe_connect(lambda: self.body_type_combo.currentIndexChanged, self._mark_dirty, "body_type_combo.currentIndexChanged")
        safe_connect(lambda: self.headers_table.itemChanged, self._mark_dirty, "headers_table.itemChanged")
        safe_connect(lambda: self.headers_table.itemChanged, self._update_tab_labels, "headers_table.itemChanged->update_tab_labels")
        safe_connect(lambda: self.params_table.itemChanged, self._mark_dirty, "params_table.itemChanged")
        safe_connect(lambda: self.params_table.itemChanged, self._update_tab_labels, "params_table.itemChanged->update_tab_labels")
        safe_connect(lambda: self.params_table.itemChanged, self._update_url_suffix, "params_table.itemChanged->update_url_suffix")
        safe_connect(lambda: self.url_input.textChanged, self._update_url_suffix, "url_input.textChanged->update_url_suffix")
        safe_connect(lambda: self.path_params_table.paramsChanged, self._mark_dirty, "path_params_table.paramsChanged")
        safe_connect(lambda: self._multipart_table.itemChanged, self._mark_dirty, "_multipart_table.itemChanged")
        safe_connect(lambda: self._multipart_table.itemChanged, self._update_tab_labels, "_multipart_table.itemChanged->update_tab_labels")
        safe_connect(lambda: self.timeout_spin.valueChanged, self._mark_dirty, "timeout_spin.valueChanged")
        safe_connect(lambda: self.verify_ssl_check.stateChanged, self._mark_dirty, "verify_ssl_check.stateChanged")
        safe_connect(lambda: self.follow_redirects_check.stateChanged, self._mark_dirty, "follow_redirects_check.stateChanged")
        safe_connect(lambda: self.notes_editor.textChanged, self._mark_dirty, "notes_editor.textChanged")
        safe_connect(lambda: self._gql_query.textChanged, self._mark_dirty, "_gql_query.textChanged")
        safe_connect(lambda: self._gql_vars.textChanged, self._mark_dirty, "_gql_vars.textChanged")

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
            logger.warning("Failed to refresh URL completer", exc_info=True)

    # ── UI construction ───────────────────────────────────────────────

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 4)
        layout.setSpacing(4)

        layout.addLayout(self._build_url_bar())

        self._preflight_banner = self._build_preflight_banner()
        layout.addWidget(self._preflight_banner)

        self.url_input.textChanged.connect(self._on_url_changed_for_path_params)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_headers_tab(), "Headers")
        self.tabs.addTab(self._build_params_tab(), "Params")
        self.tabs.addTab(self._build_body_tab(), "Body")
        self.tabs.addTab(self._create_auth_tab(), "Auth")
        self.tabs.addTab(self._create_captures_tab(), "Captures")
        self.tabs.addTab(self._create_scripts_tab(), "Scripts")
        self.tabs.addTab(self._create_settings_tab(), "Settings")
        self.tabs.addTab(self._build_notes_tab(), "Notes")
        self.tabs.addTab(self._create_assertions_tab(), "Assertions")
        layout.addWidget(self.tabs, 1)

        layout.addLayout(self._build_bottom_bar())

    # ── UI sub-builders (called once from _init_ui) ────────────────────

    def _build_url_bar(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(4)
        self.method_combo = QComboBox()
        self.method_combo.addItems(["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
        self.method_combo.setFixedWidth(90)
        self.url_input = UrlLineEdit()
        self.url_input.setPlaceholderText(
            "https://api.example.com/v1/resource  ·  {{VAR}} for variables  ·  Ctrl+N = new"
        )
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
        row.addWidget(self.method_combo)
        row.addWidget(self.url_input, 1)
        row.addWidget(self.send_button)
        row.addWidget(self.cancel_button)
        return row

    def _build_bottom_bar(self) -> QHBoxLayout:
        """Bottom toolbar with save/import/benchmark controls and session-vars summary.

        Kept intentionally lightweight so unit tests and the main window can
        instantiate the panel without depending on platform-specific UI state.
        """
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)

        save_btn = QPushButton("Save")
        save_btn.setFixedWidth(80)
        save_btn.clicked.connect(self._save_request)

        import_btn = QPushButton("Import from cURL")
        import_btn.setFixedWidth(140)
        import_btn.clicked.connect(self._import_from_curl)

        bench_btn = QPushButton("Benchmark")
        bench_btn.setFixedWidth(100)
        bench_btn.clicked.connect(self._open_benchmark)

        clear_sv_btn = QPushButton("Clear Session Vars")
        clear_sv_btn.setFixedWidth(140)
        clear_sv_btn.clicked.connect(self.clear_session_vars)

        self._session_vars_label = QLabel("Session vars: 0")
        self._session_vars_label.setObjectName("mutedLabel")

        row.addWidget(save_btn)
        row.addWidget(import_btn)
        row.addWidget(bench_btn)
        row.addWidget(clear_sv_btn)
        row.addStretch()
        row.addWidget(self._session_vars_label)

        # Update session-vars count whenever the signal is emitted
        try:
            self.session_vars_changed.connect(
                lambda d: self._session_vars_label.setText(f"Session vars: {len(d)}")
            )
        except Exception:
            # Best-effort — don't let signal wiring break UI init
            logger.debug("Could not connect session_vars_changed signal", exc_info=True)

        return row

    def _build_preflight_banner(self) -> QWidget:
        banner = QWidget()
        banner.setObjectName("preflightBanner")
        pf_row = QHBoxLayout(banner)
        pf_row.setContentsMargins(6, 2, 4, 2)
        pf_row.setSpacing(6)
        self._preflight_label = QLabel("")
        self._preflight_label.setStyleSheet(f"color: {Colors.AMBER}; font-weight: bold;")
        self._preflight_label.setWordWrap(True)
        pf_dismiss = QToolButton()
        pf_dismiss.setText("✕")
        pf_dismiss.setFixedSize(20, 20)
        pf_dismiss.setStyleSheet("border: none;")
        pf_dismiss.clicked.connect(lambda: banner.setVisible(False))
        pf_row.addWidget(self._preflight_label, 1)
        pf_row.addWidget(pf_dismiss)
        banner.setVisible(False)
        return banner

    def _build_kv_tab(
        self,
        title: str,
        *,
        presets=None,
        enable_key_completer: bool = False,
    ) -> tuple:
        """Shared boilerplate for Headers / Params tabs.

        Builds a widget with a :class:`TabToolbar` and a
        :class:`CheckableKeyValueTable`, wiring the common add / remove /
        enable-all / disable-all toolbar signals to table-generic helpers.

        Returns ``(widget, layout, toolbar, table)`` so callers can
        post-configure — e.g. connect extra toolbar signals or append
        additional sections to the layout.
        """
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(0, 2, 0, 0)
        layout.setSpacing(2)
        toolbar = TabToolbar(title, presets=presets, parent=self)
        table = CheckableKeyValueTable(enable_key_completer=enable_key_completer)
        toolbar.add_clicked.connect(lambda: self._add_row_and_focus(table))
        toolbar.remove_clicked.connect(lambda: self._remove_table_rows(table))
        toolbar.enable_all_clicked.connect(lambda: self._set_all_checkable(table, True))
        toolbar.disable_all_clicked.connect(lambda: self._set_all_checkable(table, False))
        layout.addWidget(toolbar)
        layout.addWidget(table, 1)
        return w, layout, toolbar, table

    def _build_headers_tab(self) -> QWidget:
        w, _, toolbar, self.headers_table = self._build_kv_tab(
            "Headers", presets=_HEADER_PRESETS, enable_key_completer=True
        )
        toolbar.preset_selected.connect(self._insert_header_preset)
        return w

    def _build_params_tab(self) -> QWidget:
        w, layout, _, self.params_table = self._build_kv_tab("Query Parameters")
        self._path_params_widget = QWidget()
        pp_inner = QVBoxLayout(self._path_params_widget)
        pp_inner.setContentsMargins(0, 6, 0, 0)
        pp_inner.setSpacing(2)
        pp_label = QLabel("Path Parameters")
        pp_label.setStyleSheet("font-weight: bold;")
        pp_inner.addWidget(pp_label)
        self.path_params_table = PathParamsTable()
        pp_inner.addWidget(self.path_params_table)
        self._path_params_widget.setVisible(False)
        layout.addWidget(self._path_params_widget, 1)
        return w

    def _build_body_tab(self) -> QWidget:
        from equinox.gui.syntax_highlighter import JsonHighlighter
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(0, 4, 0, 0)
        type_bar = QHBoxLayout()
        self.body_type_combo = QComboBox()
        self.body_type_combo.addItems(
            ["none", "raw (JSON)", "raw (XML)", "raw (text)",
             "form-urlencoded", "multipart/form-data", "GraphQL"]
        )
        self.body_type_combo.currentIndexChanged.connect(self._on_body_type_changed)
        type_bar.addWidget(QLabel("Type:"))
        type_bar.addWidget(self.body_type_combo)
        self._fmt_json_btn = QPushButton("Format JSON")
        self._fmt_json_btn.setFixedWidth(95)
        self._fmt_json_btn.setToolTip("Pretty-print the JSON body (Ctrl+Shift+F)")
        self._fmt_json_btn.clicked.connect(self._format_json_body)
        self._fmt_json_btn.setVisible(False)
        type_bar.addWidget(self._fmt_json_btn)
        # Inline search controls for the body editor: term + options
        self._body_search_input = QLineEdit()
        self._body_search_input.setPlaceholderText("Find in body…")
        self._body_search_input.setFixedHeight(26)
        self._body_search_input.setClearButtonEnabled(True)
        self._body_search_input.returnPressed.connect(lambda: self._body_find_next())
        self._body_search_input.textChanged.connect(lambda: self._body_highlight_all())

        self._body_case_cb = QCheckBox("Aa")
        self._body_case_cb.setToolTip("Case-sensitive")
        self._body_case_cb.setFixedWidth(36)
        self._body_regex_cb = QCheckBox(".*")
        self._body_regex_cb.setToolTip("Use regular expression")
        self._body_regex_cb.setFixedWidth(36)
        self._body_jsonpath_cb = QCheckBox("$.")
        self._body_jsonpath_cb.setToolTip("Interpret search as JSON path (dot/bracket syntax)")
        self._body_jsonpath_cb.setFixedWidth(36)

        prev_btn = QToolButton(); prev_btn.setText("▲"); prev_btn.setFixedSize(24, 24)
        next_btn = QToolButton(); next_btn.setText("▼"); next_btn.setFixedSize(24, 24)
        prev_btn.clicked.connect(lambda: self._body_find_prev())
        next_btn.clicked.connect(lambda: self._body_find_next())

        # Pack search controls into a horizontal layout placed under the type bar
        search_row = QHBoxLayout()
        search_row.setContentsMargins(0, 2, 0, 0)
        search_row.setSpacing(4)
        search_row.addWidget(QLabel("Find:"))
        search_row.addWidget(self._body_search_input, 1)
        search_row.addWidget(self._body_case_cb)
        search_row.addWidget(self._body_regex_cb)
        search_row.addWidget(self._body_jsonpath_cb)
        search_row.addWidget(prev_btn)
        search_row.addWidget(next_btn)
        layout.addLayout(search_row)

        # (multipart controls are created once later as a top toolbar so
        # controls align with other tab toolbars)
        # Parent the editor to the RequestPanel to ensure Qt owns the
        # underlying C++ object and it isn't deleted unexpectedly by the
        # Python GC while the wrapper remains referenced.
        # Using `self` as parent is safe: the widget will still be
        # reparented into the body tab layout and will remain alive for
        # the lifetime of the panel.
        # Create the real editor and wrap it in a proxy that tolerates
        # a missing underlying C++ object in some headless test environments.
        real_body = JsonBodyEditor(self)
        proxy = BodyTextProxy(self, real_body)
        # Add the real widget to the layout (QLayout expects a QWidget)
        layout.addWidget(real_body)
        # Expose the proxy as the public attribute so callers go through it
        self.body_text = proxy
        self.body_text.setPlaceholderText('{ "key": "value" }')
        self.body_text.setFont(get_mono_font())
        self._body_highlighter = JsonHighlighter(self.body_text.document())
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
        layout.addWidget(self._multipart_table)
        # ...existing code... (multipart controls moved to the top toolbar)
        self._gql_widget = QWidget()
        layout.addWidget(self._gql_widget)
        gql_layout = QVBoxLayout(self._gql_widget)
        gql_layout.setContentsMargins(0, 4, 0, 0)
        gql_splitter = QSplitter(Qt.Orientation.Vertical)
        q_group = QGroupBox("Query")
        q_lay = QVBoxLayout(q_group)
        q_lay.setContentsMargins(4, 6, 4, 4)
        self._gql_query = QPlainTextEdit()
        self._gql_query.setPlaceholderText("query {\n  users {\n    id\n    name\n  }\n}")
        self._gql_query.setFont(get_mono_font())
        q_lay.addWidget(self._gql_query)
        v_group = QGroupBox("Variables (JSON, optional)")
        v_lay = QVBoxLayout(v_group)
        v_lay.setContentsMargins(4, 6, 4, 4)
        self._gql_vars = QPlainTextEdit()
        self._gql_vars.setPlaceholderText('{\n  "id": 1\n}')
        self._gql_vars.setFont(get_mono_font())
        v_lay.addWidget(self._gql_vars)
        gql_splitter.addWidget(q_group)
        gql_splitter.addWidget(v_group)
        gql_splitter.setSizes([200, 120])
        gql_layout.addWidget(gql_splitter, 1)
        self._fmt_json_btn.setVisible(False)
        type_bar.addWidget(self._fmt_json_btn)

        # Multipart toolbar (consistent with other tab toolbars)
        self._mp_toolbar = TabToolbar("", include_file_btn=True, parent=self)
        self._mp_toolbar.add_clicked.connect(self._multipart_add_row)
        self._mp_toolbar.remove_clicked.connect(self._multipart_remove_row)
        self._mp_toolbar.file_browse_clicked.connect(self._multipart_browse_file)
        self._mp_toolbar.setVisible(False)
        layout.addWidget(self._mp_toolbar)

        type_bar.addStretch()
        layout.addLayout(type_bar)
        return w

    def _build_notes_tab(self) -> QWidget:
        """Notes tab: free-form description for the request."""
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(4, 6, 4, 4)
        layout.addWidget(QLabel("Notes / description for this request:"))
        self.notes_editor = QPlainTextEdit()
        self.notes_editor.setPlaceholderText(
            "Add notes, cURL examples, API docs links, or any context about this request…"
        )
        layout.addWidget(self.notes_editor, 1)
        return w

    def _build_script_section(self, title: str, placeholder: str) -> tuple:
        """Build a labelled script-editor group box.

        Returns ``(group, editor, result_label)`` so the caller can assign
        the widgets to instance attributes and add the group to a splitter.
        """
        group = QGroupBox(title)
        grp_layout = QVBoxLayout(group)
        grp_layout.setContentsMargins(4, 6, 4, 4)
        editor = QPlainTextEdit()
        editor.setPlaceholderText(placeholder)
        editor.setFont(get_mono_font())
        result_label = QLabel("")
        result_label.setWordWrap(True)
        grp_layout.addWidget(editor)
        grp_layout.addWidget(result_label)
        return group, editor, result_label

    def _create_scripts_tab(self) -> QWidget:
        """Single tab with Pre-request and Post-response script editors."""
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(4, 6, 4, 4)

        splitter = QSplitter(Qt.Orientation.Vertical)

        pre_group, self.pre_script_editor, self.pre_script_result = (
            self._build_script_section(
                "Pre-request Script",
                "# Runs before the request is sent\n"
                "# Available: request (dict), env (dict)\n"
                "# Example: env['timestamp'] = str(int(__import__('time').time()))",
            )
        )
        splitter.addWidget(pre_group)

        post_group, self.post_script_editor, self.post_script_result = (
            self._build_script_section(
                "Post-response Script",
                "# Runs after response is received\n"
                "# Available: response (dict with status_code, headers, body, json), env (dict)\n"
                "# Example: env['user_id'] = str(response['json']['id'])",
            )
        )
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
        cheat_toggle = QPushButton()
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

        return w

    def _create_settings_tab(self) -> QWidget:
        """Settings tab: timeout, SSL, redirects, and client certificate."""
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(4, 6, 4, 4)

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
        layout.addStretch()
        return w

    def _browse_file_to_input(self, title: str, filters: str, target) -> None:
        """Open a file-picker dialog and write the chosen path into *target* QLineEdit."""
        path, _ = QFileDialog.getOpenFileName(self, title, "", filters)
        if path:
            target.setText(path)

    def _browse_cert(self) -> None:
        self._browse_file_to_input(
            "Select Certificate File",
            "Certificate files (*.pem *.crt *.cer);;All files (*)",
            self.cert_path_input,
        )

    def _browse_cert_key(self) -> None:
        self._browse_file_to_input(
            "Select Private Key File",
            "Key files (*.pem *.key);;All files (*)",
            self.cert_key_input,
        )

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

    # ── Headers / params bulk actions and presets ─────────────────────

    @staticmethod
    def _set_all_checkable(table, enabled: bool) -> None:
        """Enable or disable every row in a checkable key-value table."""
        state = Qt.CheckState.Checked if enabled else Qt.CheckState.Unchecked
        for row in range(table.rowCount()):
            chk_item = table.item(row, 0)
            if chk_item is not None:
                chk_item.setCheckState(state)

    def _insert_header_preset(self, key: str, value: str) -> None:
        """Append a header preset row (or update existing key)."""
        existing = self.headers_table.get_all_rows()
        for row_data in existing:
            if row_data.get("key", "").lower() == key.lower():
                # Key already present — select the row so user can edit the value
                return
        self.headers_table.add_row(key, value)
        self._mark_dirty()
        self._update_tab_labels()

    def _add_row_and_focus(self, table: CheckableKeyValueTable) -> None:
        """Append an empty row to *table*, select its key cell, and mark dirty."""
        table.add_row("", "", enabled=True)
        last = table.rowCount() - 2  # skip trailing sentinel
        if last >= 0:
            table.setCurrentCell(last, 1)  # column 1 = Key
            item = table.item(last, 1)
            if item:
                table.editItem(item)
        self._mark_dirty()
        self._update_tab_labels()

    def _remove_table_rows(self, table) -> None:
        """Remove selected rows from *table*, then mark dirty and update labels."""
        for r in sorted({idx.row() for idx in table.selectedIndexes()}, reverse=True):
            table.removeRow(r)
        self._mark_dirty()
        self._update_tab_labels()

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
            logger.debug("Failed to update URL suffix", exc_info=True)
            self.url_input.set_param_suffix("")


    def _on_url_changed_for_path_params(self, text: str) -> None:
        """Show/hide path-params section within the Params tab."""
        self.path_params_table.update_from_url(text)
        self._path_params_widget.setVisible(self.path_params_table.rowCount() > 0)
        self._update_tab_labels()

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

    def _save_request(self) -> None:
        url = self.url_input.text().strip()
        if not url:
            QMessageBox.warning(self, "Missing URL", "Please enter a URL before saving.")
            return

        method      = self.method_combo.currentText()
        headers     = self.headers_table.get_data()
        params      = self.params_table.get_enabled_data()
        params_list = self.params_table.get_all_rows()
        body        = self.body_text.toPlainText().strip() or None

        current_folder = ""
        if self.current_request and getattr(self.current_request, "folder", None):
            current_folder = self.current_request.folder

        try:
            dlg = SaveRequestDialog(self.db, method, url, current_folder, parent=self)
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Could not open save dialog: {exc}")
            return

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        name, col_id, col_name, folder = dlg.result_values()

        from equinox.storage import CollectionManager
        mgr = CollectionManager(self.db)
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
            req_id = mgr.save_request(request, collection_id=col_id, name=name)
            # Link the editor to the newly-saved DB row so autosave targets it.
            request.id = req_id
            request.collection_id = col_id
            self.current_request = request
            self._dirty = False
            self._status_message(f"Saved '{name}' to '{col_name}'")
            try:
                win = self.window()
                if hasattr(win, 'collections_panel'):
                    win.collections_panel.refresh()
            except Exception:
                logger.warning("Failed to refresh collections panel after save", exc_info=True)
        except Exception as exc:
            QMessageBox.critical(self, "Save Failed", str(exc))


# ─────────────────────────────────────────────────────────────────────────────
# Backward-compat aliases — use core.error_enrichment directly in new code
# ─────────────────────────────────────────────────────────────────────────────

_RichError = RichError
_enrich_exception = enrich_exception
