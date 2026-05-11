"""Request builder panel.

Logging strategy:
- Entry/exit: major operations (load_request, send_request, save_request)
- Context: method, URL, request_id for structured logs
- Errors: full exception context with request details
"""

import json
import logging
import time
from typing import Optional, Dict, NamedTuple, Tuple

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QComboBox,
    QLineEdit,
    QInputDialog,
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
    QFormLayout,
    QToolButton,
    QApplication,
)
from PyQt6.QtCore import pyqtSignal, Qt, QTimer, QStringListModel, QSettings
from PyQt6.QtGui import QKeySequence, QShortcut

from equinox.gui.request_panel.body_text_proxy import BodyTextProxy
from equinox.gui.theme import get_mono_font
from equinox.gui.widgets import UrlLineEdit, CheckableKeyValueTable, JsonBodyEditor, PathParamsTable
from equinox.core.request import Request
from equinox.core.error_enrichment import RichError, enrich_exception  # noqa: F401 (used in mixin layer)
from equinox.storage import Database, HistoryManager, CollectionManager
from equinox.gui.workers import RequestWorker, BenchmarkDialog, DEFAULT_TIMEOUT  # noqa: F401 (RequestWorker used as type annotation)
from equinox.core.cookies import CookieManager
from equinox.gui.request_panel.mixins import (  # noqa: F401
    _RequestSendMixin,
    _RequestAuthMixin,
    _save_history_safe,
)
from equinox.gui.request_panel._constants import (
    BENCHMARK_BTN_WIDTH,
    BROWSE_BTN_WIDTH,
    CANCEL_BTN_WIDTH,
    CLEAR_SV_BTN_WIDTH,
    COMPLETER_MAX_VISIBLE,
    FMT_JSON_BTN_WIDTH,
    HISTORY_COMPLETER_LIMIT,
    IMPORT_BTN_WIDTH,
    METHOD_COMBO_WIDTH,
    SEND_BTN_WIDTH,
    STATUS_DURATION_LONG,
)
from equinox.gui.request_panel.body_mixin import RequestBodyMixin  # noqa: F401
from equinox.gui.request_panel.validation_mixin import _RequestValidationMixin  # noqa: F401
from equinox.gui.request_panel.save_dialog import SaveRequestDialog
from equinox.gui.request_panel.toolbar import TabToolbar
from equinox.gui.syntax_highlighter.python_highlighter import PythonHighlighter

logger = logging.getLogger(__name__)
_KEY_POLICY_PROFILE = "request/policy_profile"
_POLICY_STRICT = "Strict"
_POLICY_BALANCED = "Balanced"
_POLICY_PERMISSIVE = "Permissive"


__all__ = ["RequestPanel"]

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

# HTML cheat-sheet shown in the collapsible Scripts tab section
_SCRIPTS_CHEAT_TEXT = (
    "<b>Context objects</b><br>"
    "&nbsp;&nbsp;<code>env</code> — dict of active environment variables (read/write)<br>"
    "&nbsp;&nbsp;<code>request</code> — dict: method, url, headers, body (pre-script only)<br>"
    "&nbsp;&nbsp;<code>response</code> — dict: status_code, headers, body, json (post-script only)<br>"
    "<br><b>Allowed modules</b><br>"
    "&nbsp;&nbsp;json, re, base64, hashlib, hmac, datetime, time, math, uuid, urllib.parse"
)


class _KvTabResult(NamedTuple):
    """Return type for ``_build_kv_tab`` — avoids anonymous 4-tuples."""
    widget: QWidget
    layout: QVBoxLayout
    toolbar: TabToolbar
    table: CheckableKeyValueTable


# ─────────────────────────────────────────────────────────────────────────────
# Request panel
# ─────────────────────────────────────────────────────────────────────────────

class RequestPanel(_RequestValidationMixin, _RequestSendMixin, _RequestAuthMixin, RequestBodyMixin, QWidget):
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

    def __init__(self, db: Database, parent=None, cookie_manager: Optional[CookieManager]=None):
        super().__init__(parent)
        logger.debug("RequestPanel.__init__ starting")
        self.db = db
        self._cookie_manager: Optional[CookieManager] = cookie_manager

        self._collection_mgr: CollectionManager = CollectionManager(db)
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
        self._settings = QSettings("Equinox", "Equinox")
        self._policy_profile = str(self._settings.value(_KEY_POLICY_PROFILE, _POLICY_BALANCED))

        self._init_ui()
        self._setup_dirty_tracking()
        self._setup_url_completer()
        self._init_validation()  # Initialize real-time validation

        # Ctrl+Enter sends from anywhere in the panel (#8)
        send_shortcut = QShortcut(QKeySequence("Ctrl+Return"), self)
        send_shortcut.activated.connect(self._send_request)

        # Ctrl+S saves to collection from anywhere in the panel
        save_shortcut = QShortcut(QKeySequence("Ctrl+S"), self)
        save_shortcut.activated.connect(self._save_request)

        # Ctrl+L focuses and selects the URL field for fast keyboard editing.
        focus_url_shortcut = QShortcut(QKeySequence("Ctrl+L"), self)
        focus_url_shortcut.activated.connect(self._focus_url_input)

        # Ctrl+Shift+F formats JSON body (#6)
        fmt_shortcut = QShortcut(QKeySequence("Ctrl+Shift+F"), self)
        fmt_shortcut.activated.connect(self._format_json_body)

        logger.info("RequestPanel initialized successfully")

    # ── Dirty-flag tracking (#3) ──────────────────────────────────────

    def is_dirty(self) -> bool:
        return self._dirty

    def _mark_dirty(self) -> None:
        self._dirty = True

    # ── Autosave ──────────────────────────────────────────────────────

    def _build_request_from_editor(self, **overrides) -> Request:
        """Construct a :class:`Request` from the current editor widget state.

        Common extraction point used by autosave, save-to-collection, and
        the send path so field reading is defined in exactly one place.

        Args:
            **overrides: Keyword arguments passed through to the ``Request``
                constructor, overriding the values read from widgets.  Typical
                overrides include ``name``, ``collection_id``, ``folder``,
                ``id``, ``auth``, and ``multipart_data``.

        Returns:
            A fully populated :class:`Request` instance.
        """
        fields = dict(
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
            captures=self._get_captures(),
            assertions=self._get_assertions(),
            pre_script=self.pre_script_editor.toPlainText(),
            post_script=self.post_script_editor.toPlainText(),
            cert_path=self.cert_path_input.text().strip() or None,
            cert_key_path=self.cert_key_input.text().strip() or None,
            description=self.notes_editor.toPlainText().strip() or None,
            path_params=self.path_params_table.get_all_data(),
        )
        fields.update(overrides)
        return Request(**fields)

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
            updated = self._build_request_from_editor(
                name=req.name,
                collection_id=req.collection_id,
                folder=req.folder,
                id=req.id,
            )
            self._collection_mgr.update_request(updated)
            self._clear_dirty()
            logger.debug("Autosaved request id=%s %s %s", req.id, updated.method, updated.url)
        except Exception:
            logger.error("Autosave failed for request id=%s", getattr(req, "id", None), exc_info=True)
            self._status_message("⚠ Autosave failed — click Save to preserve changes", STATUS_DURATION_LONG)

    # ── Session variable accessors ─────────────────────────────────────

    def get_session_vars(self) -> Dict[str, str]:
        """Return a copy of the current session variables."""
        return dict(self._session_vars)

    def clear_session_vars(self) -> None:
        """Clear all session variables and notify listeners."""
        self._session_vars.clear()
        self.session_vars_changed.emit({})

    def _clear_dirty(self) -> None:
        self._dirty = False

    def send(self) -> None:
        """Public wrapper for sending the current request.

        External callers (e.g. other GUI panels) should call this instead of
        invoking the private ``_send_request`` method directly.
        """
        self._send_request()

    def _setup_dirty_tracking(self) -> None:
        """Connect change signals on all editor widgets to mark dirty."""
        _connected = 0

        def safe_connect(get_signal, slot, name=None):
            """Lazily retrieve a signal via get_signal() and connect it to slot."""
            nonlocal _connected
            try:
                sig = get_signal()
            except (AttributeError, RuntimeError) as exc:
                logger.debug(
                    "Signal retrieval skipped (C++ object missing): %s — %s",
                    name, type(exc).__name__,
                )
                return
            except Exception:
                logger.warning(
                    "Unexpected error retrieving signal: %s", name, exc_info=True
                )
                return
            try:
                sig.connect(slot)
                _connected += 1
            except RuntimeError:
                logger.debug("Failed to connect signal after retrieval: %s", name)

        safe_connect(lambda: self.url_input.textChanged, self._mark_dirty, "url_input.textChanged")
        safe_connect(lambda: self.method_combo.currentIndexChanged, self._mark_dirty, "method_combo.currentIndexChanged")
        safe_connect(lambda: self.body_text.textChanged, self._mark_dirty, "body_text.textChanged")
        safe_connect(lambda: self.body_text.textChanged, self._update_tab_labels, "body_text.textChanged->update_tab_labels")
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
        safe_connect(lambda: self.verify_ssl_check.stateChanged, lambda: self._update_auth_display(self._auth), "verify_ssl_check.stateChanged->auth_display")
        safe_connect(lambda: self.follow_redirects_check.stateChanged, self._mark_dirty, "follow_redirects_check.stateChanged")
        safe_connect(lambda: self.url_input.textChanged, lambda: self._update_auth_display(self._auth), "url_input.textChanged->auth_display")
        safe_connect(lambda: self.notes_editor.textChanged, self._mark_dirty, "notes_editor.textChanged")
        safe_connect(lambda: self._gql_query.textChanged, self._mark_dirty, "_gql_query.textChanged")
        safe_connect(lambda: self._gql_vars.textChanged, self._mark_dirty, "_gql_vars.textChanged")
        logger.debug("Dirty tracking: %d signal(s) connected", _connected)

    # ── URL auto-complete from history (#6) ───────────────────────────

    def _setup_url_completer(self) -> None:
        self._url_model = QStringListModel(self)
        self._known_urls: set = set()
        self._url_values: list = []
        completer = QCompleter(self._url_model, self)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        completer.setMaxVisibleItems(COMPLETER_MAX_VISIBLE)
        self.url_input.setCompleter(completer)
        # Defer the DB fetch so it doesn't block the main thread during window init.
        QTimer.singleShot(0, self._refresh_url_completer)

    def _refresh_url_completer(self) -> None:
        """Populate the completer model from recent history URLs."""
        t0 = time.perf_counter()
        try:
            entries = HistoryManager(self.db).list_history(limit=HISTORY_COMPLETER_LIMIT)
            urls = [e["url"] for e in entries if e.get("url")]
            # Deduplicate while preserving order (most-recent first from history).
            unique_urls = list(dict.fromkeys(urls))
            self._known_urls = set(unique_urls)
            self._url_values = unique_urls
            self._url_model.setStringList(self._url_values)
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            logger.debug(
                "request_panel.url_completer_refreshed op=refresh_url_completer history_entries=%d url_count=%d elapsed_ms=%d",
                len(entries),
                len(self._url_values),
                elapsed_ms,
            )
        except Exception:
            logger.debug("Failed to refresh URL completer", exc_info=True)

    def _add_url_to_completer(self, url: str) -> None:
        """Incrementally add a URL to the completer without re-querying history."""
        cleaned = (url or "").strip()
        if not cleaned or cleaned in self._known_urls:
            return
        self._known_urls.add(cleaned)
        # Keep most-recent URLs near the top while honoring history limit.
        self._url_values.insert(0, cleaned)
        if len(self._url_values) > HISTORY_COMPLETER_LIMIT:
            dropped = self._url_values.pop()
            self._known_urls.discard(dropped)
        self._url_model.setStringList(self._url_values)

    def _focus_url_input(self) -> None:
        """Focus URL input and select its full text (browser-like behavior)."""
        self.url_input.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self.url_input.selectAll()

    # ── UI construction ───────────────────────────────────────────────

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        url_container = QWidget()
        url_layout = QVBoxLayout(url_container)
        url_layout.setContentsMargins(6, 6, 6, 0)
        url_layout.addLayout(self._build_url_bar())
        self._url_hint_label = QLabel("")
        self._url_hint_label.setObjectName("mutedLabel")
        self._url_hint_label.setVisible(False)
        url_layout.addWidget(self._url_hint_label)
        layout.addWidget(url_container)

        self._preflight_banner = self._build_preflight_banner()
        layout.addWidget(self._preflight_banner)

        self.url_input.textChanged.connect(self._on_url_changed_for_path_params)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_headers_tab(), "Headers")
        self.tabs.addTab(self._build_params_tab(), "Params")
        self.tabs.addTab(self._build_body_tab(), "Body")
        self.tabs.addTab(self._create_auth_tab(), "Auth")           # defined in _RequestAuthMixin
        self.tabs.addTab(self._create_captures_tab(), "Captures")   # defined in RequestBodyMixin
        self.tabs.addTab(self._create_assertions_tab(), "Assertions")  # defined in RequestBodyMixin
        self.tabs.addTab(self._create_scripts_tab(), "Scripts")
        self.tabs.addTab(self._create_settings_tab(), "Settings")
        self.tabs.addTab(self._build_notes_tab(), "Notes")
        layout.addWidget(self.tabs, 1)

        bottom_container = QWidget()
        bottom_layout = QVBoxLayout(bottom_container)
        bottom_layout.setContentsMargins(6, 0, 6, 6)
        bottom_layout.addLayout(self._build_bottom_bar())
        layout.addWidget(bottom_container)

    # ── UI sub-builders (called once from _init_ui) ────────────────────

    def _build_url_bar(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(4)
        self.method_combo = QComboBox()
        self.method_combo.addItems(["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
        self.method_combo.setFixedWidth(METHOD_COMBO_WIDTH)
        self.url_input = UrlLineEdit()
        self.url_input.setPlaceholderText(
            "https://api.example.com/v1/resource  ·  {{VAR}} for variables  ·  Ctrl+N = new"
        )
        self.url_input.returnPressed.connect(self._send_request)
        self._url_fix_button = QToolButton()
        self._url_fix_button.setText("Fix URL")
        self._url_fix_button.setToolTip("Apply suggested URL fix")
        self._url_fix_button.clicked.connect(self._apply_url_fix)
        self._url_fix_button.setVisible(False)
        self._url_fix_suggestion = None
        self.send_button = QPushButton("Send")
        self.send_button.setObjectName("sendBtn")
        self.send_button.setMinimumWidth(SEND_BTN_WIDTH)
        self.send_button.setToolTip("Send request (Ctrl+Enter)")
        self.send_button.clicked.connect(self._send_request)
        self.send_button.setDefault(True)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setObjectName("cancelBtn")
        self.cancel_button.setMinimumWidth(CANCEL_BTN_WIDTH)
        self.cancel_button.setToolTip("Cancel the in-flight request")
        self.cancel_button.clicked.connect(self._cancel_request)
        self.cancel_button.setVisible(False)
        row.addWidget(self.method_combo)
        row.addWidget(self.url_input, 1)
        row.addWidget(self._url_fix_button)
        row.addWidget(self.send_button)
        row.addWidget(self.cancel_button)
        return row

    def _set_url_validation_hint(self, message: str, is_error: bool = False) -> None:
        """Show a small inline hint below the URL field."""
        msg = (message or "").strip()
        self._url_hint_label.setVisible(bool(msg))
        self._url_hint_label.setText(msg)
        self._url_hint_label.setObjectName("field-error" if is_error else "mutedLabel")

    def _set_url_fix_suggestion(self, suggestion: Optional[str], reason: str = "") -> None:
        """Expose a one-click URL fix when validation can safely auto-correct."""
        self._url_fix_suggestion = suggestion
        can_fix = bool(suggestion)
        self._url_fix_button.setVisible(can_fix)
        if can_fix:
            self._url_fix_button.setToolTip(reason or "Apply suggested URL fix")

    def _apply_url_fix(self) -> None:
        """Apply the pending URL fix suggestion, if available."""
        if not self._url_fix_suggestion:
            return
        self.url_input.setText(self._url_fix_suggestion)
        self._set_url_fix_suggestion(None)
        self._set_url_validation_hint("URL auto-correct applied.", is_error=False)

    def _build_bottom_bar(self) -> QHBoxLayout:
        """Bottom toolbar with save/import/benchmark controls and session-vars summary.

        Kept intentionally lightweight so unit tests and the main window can
        instantiate the panel without depending on platform-specific UI state.
        """
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)

        save_btn = QPushButton("Save")
        save_btn.setMinimumWidth(SEND_BTN_WIDTH)
        save_btn.setToolTip("Save to a collection (prompts for name / folder)")
        save_btn.clicked.connect(self._save_request)

        import_btn = QPushButton("Import from cURL")
        import_btn.setMinimumWidth(IMPORT_BTN_WIDTH)
        import_btn.setToolTip("Paste a cURL command to populate the request editor")
        import_btn.clicked.connect(self._import_from_curl)

        bench_btn = QPushButton("Benchmark")
        bench_btn.setMinimumWidth(BENCHMARK_BTN_WIDTH)
        bench_btn.setToolTip("Run repeated requests and view latency statistics")
        bench_btn.clicked.connect(self._open_benchmark)

        clear_sv_btn = QPushButton("Clear Session Vars")
        clear_sv_btn.setMinimumWidth(CLEAR_SV_BTN_WIDTH)
        clear_sv_btn.setToolTip("Clear all captured session variables")
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
        self.session_vars_changed.connect(
            lambda d: self._session_vars_label.setText(f"Session vars: {len(d)}")
        )

        return row

    def _build_preflight_banner(self) -> QWidget:
        banner = QWidget()
        banner.setObjectName("preflightBanner")
        pf_row = QHBoxLayout(banner)
        pf_row.setContentsMargins(6, 2, 4, 2)
        pf_row.setSpacing(6)
        self._preflight_label = QLabel("")
        self._preflight_label.setWordWrap(True)
        pf_dismiss = QToolButton()
        pf_dismiss.setText("✕")
        pf_dismiss.setFixedSize(20, 20)
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
    ) -> _KvTabResult:
        """Shared boilerplate for Headers / Params tabs.

        Builds a widget with a :class:`TabToolbar` and a
        :class:`CheckableKeyValueTable`, wiring the common add / remove /
        enable-all / disable-all toolbar signals to table-generic helpers.

        Returns a :class:`_KvTabResult` so callers can post-configure —
        e.g. connect extra toolbar signals or append additional sections.
        """
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(0, 2, 0, 0)
        layout.setSpacing(2)
        toolbar = TabToolbar("", presets=presets, parent=self)
        table = CheckableKeyValueTable(enable_key_completer=enable_key_completer)
        toolbar.add_clicked.connect(lambda: self._add_row_and_focus(table))
        toolbar.remove_clicked.connect(lambda: self._remove_table_rows(table))
        toolbar.enable_all_clicked.connect(lambda: self._set_all_checkable(table, True))
        toolbar.disable_all_clicked.connect(lambda: self._set_all_checkable(table, False))
        layout.addWidget(toolbar)
        layout.addWidget(table, 1)
        return _KvTabResult(w, layout, toolbar, table)

    def _build_headers_tab(self) -> QWidget:
        result = self._build_kv_tab(
            "Headers", presets=_HEADER_PRESETS, enable_key_completer=True
        )
        self.headers_table = result.table
        result.toolbar.preset_selected.connect(self._insert_header_preset)
        return result.widget

    def _build_params_tab(self) -> QWidget:
        result = self._build_kv_tab("Query Parameters")
        self.params_table = result.table
        self._path_params_widget = QWidget()
        pp_inner = QVBoxLayout(self._path_params_widget)
        pp_inner.setContentsMargins(0, 6, 0, 0)
        pp_inner.setSpacing(2)
        pp_label = QLabel("Path Parameters")
        pp_inner.addWidget(pp_label)
        self.path_params_table = PathParamsTable()
        pp_inner.addWidget(self.path_params_table)
        self._path_params_widget.setVisible(False)
        result.layout.addWidget(self._path_params_widget, 1)
        return result.widget

    def _build_body_tab(self) -> QWidget:
        """Body tab: type selector, inline search, raw editor, multipart, and GraphQL."""
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(0, 4, 0, 0)

        layout.addLayout(self._build_body_type_bar())
        layout.addLayout(self._build_body_search_bar())
        self._build_body_editor(layout)
        self._build_multipart_section(layout)
        self._build_graphql_section(layout)

        return w

    def _build_body_type_bar(self) -> QHBoxLayout:
        """Body-type combo and format button."""
        type_bar = QHBoxLayout()
        self.body_type_combo = QComboBox()
        self.body_type_combo.addItems(
            ["none", "raw (JSON)", "raw (XML)", "raw (text)",
             "form-urlencoded", "multipart/form-data", "GraphQL"]
        )
        # _on_body_type_changed is defined in RequestBodyMixin
        self.body_type_combo.currentIndexChanged.connect(self._on_body_type_changed)
        type_bar.addWidget(QLabel("Type:"))
        type_bar.addWidget(self.body_type_combo)
        self._fmt_json_btn = QPushButton("Format JSON")
        self._fmt_json_btn.setMinimumWidth(FMT_JSON_BTN_WIDTH)
        self._fmt_json_btn.setToolTip("Pretty-print the JSON body (Ctrl+Shift+F)")
        self._fmt_json_btn.clicked.connect(self._format_json_body)
        self._fmt_json_btn.setVisible(False)
        type_bar.addWidget(self._fmt_json_btn)
        type_bar.addStretch()
        return type_bar

    def _build_body_search_bar(self) -> QHBoxLayout:
        """Inline find bar for the body editor."""
        self._body_search_input = QLineEdit()
        self._body_search_input.setPlaceholderText("Find in body…")
        self._body_search_input.setFixedHeight(26)
        self._body_search_input.setClearButtonEnabled(True)

        self._body_search_input.returnPressed.connect(self._body_find_next)
        self._body_search_input.textChanged.connect(self._body_highlight_all)

        self._body_case_cb = QCheckBox("Aa")
        self._body_case_cb.setToolTip("Case-sensitive")
        self._body_case_cb.setFixedWidth(36)
        self._body_regex_cb = QCheckBox(".*")
        self._body_regex_cb.setToolTip("Use regular expression")
        self._body_regex_cb.setFixedWidth(36)
        self._body_jsonpath_cb = QCheckBox("$.")
        self._body_jsonpath_cb.setToolTip("Interpret search as JSON path (dot/bracket syntax)")
        self._body_jsonpath_cb.setFixedWidth(36)

        prev_btn = QToolButton()
        prev_btn.setText("▲")
        prev_btn.setFixedSize(24, 24)
        prev_btn.setToolTip("Find previous match")
        next_btn = QToolButton()
        next_btn.setText("▼")
        next_btn.setFixedSize(24, 24)
        next_btn.setToolTip("Find next match")

        prev_btn.clicked.connect(self._body_find_prev)
        next_btn.clicked.connect(self._body_find_next)

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
        return search_row

    def _build_body_editor(self, layout: QVBoxLayout) -> None:
        """Create the raw/text body editor and wrap it in a resilient proxy."""
        real_body = JsonBodyEditor(self)
        proxy = BodyTextProxy(self, real_body)
        layout.addWidget(real_body, 1)
        self.body_text = proxy
        self.body_text.setPlaceholderText('{ "key": "value" }')
        self.body_text.setFont(get_mono_font())

    def _build_multipart_section(self, layout: QVBoxLayout) -> None:
        """Multipart form-data toolbar and table."""
        self._mp_toolbar = TabToolbar("", include_file_btn=True, parent=self)
        self._mp_toolbar.add_clicked.connect(self._multipart_add_row)
        self._mp_toolbar.remove_clicked.connect(self._multipart_remove_row)
        self._mp_toolbar.file_browse_clicked.connect(self._multipart_browse_file)
        self._mp_toolbar.setVisible(False)
        layout.addWidget(self._mp_toolbar)

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
        layout.addWidget(self._multipart_table, 1)

    def _build_graphql_section(self, layout: QVBoxLayout) -> None:
        """GraphQL query + variables split editor."""
        self._gql_widget = QWidget()
        self._gql_widget.setVisible(False)
        layout.addWidget(self._gql_widget, 1)
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

    def _build_script_section(self, title: str, placeholder: str) -> Tuple[QGroupBox, QPlainTextEdit, QLabel]:
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
        self._pre_highlighter = PythonHighlighter(self.pre_script_editor.document())
        self._post_highlighter = PythonHighlighter(self.post_script_editor.document())

        # ── Collapsible cheat-sheet ───────────────────────────────────
        cheat_toggle = QPushButton()
        cheat_toggle.setText("▶ Available variables & modules")
        cheat_toggle.setCheckable(True)
        cheat_toggle.setFlat(True)
        layout.addWidget(cheat_toggle)

        cheat_label = QLabel(_SCRIPTS_CHEAT_TEXT)
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

        self.policy_profile_combo = QComboBox()
        self.policy_profile_combo.addItems([
            _POLICY_STRICT,
            _POLICY_BALANCED,
            _POLICY_PERMISSIVE,
        ])
        idx = self.policy_profile_combo.findText(self._policy_profile)
        if idx >= 0:
            self.policy_profile_combo.setCurrentIndex(idx)
        self.policy_profile_combo.currentTextChanged.connect(self._on_policy_profile_changed)
        settings_form.addRow("Policy profile:", self.policy_profile_combo)

        self._policy_hint = QLabel("")
        self._policy_hint.setObjectName("mutedLabel")
        self._policy_hint.setWordWrap(True)
        settings_form.addRow("", self._policy_hint)
        self._on_policy_profile_changed(self.policy_profile_combo.currentText())

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
        cert_browse.setMinimumWidth(BROWSE_BTN_WIDTH)
        cert_browse.clicked.connect(self._browse_cert)
        cert_row.addWidget(self.cert_path_input, 1)
        cert_row.addWidget(cert_browse)
        cert_layout.addLayout(cert_row)

        key_row = QHBoxLayout()
        key_row.addWidget(QLabel("Key file:"))
        self.cert_key_input = QLineEdit()
        self.cert_key_input.setPlaceholderText("Path to private key file (leave blank if combined)")
        key_browse = QPushButton("Browse…")
        key_browse.setMinimumWidth(BROWSE_BTN_WIDTH)
        key_browse.clicked.connect(self._browse_cert_key)
        key_row.addWidget(self.cert_key_input, 1)
        key_row.addWidget(key_browse)
        cert_layout.addLayout(key_row)

        layout.addWidget(cert_group)
        layout.addStretch()
        return w

    def _on_policy_profile_changed(self, profile: str) -> None:
        """Apply and persist guardrail profile selection."""
        profile = str(profile or _POLICY_BALANCED)
        self._policy_profile = profile
        try:
            self._settings.setValue(_KEY_POLICY_PROFILE, profile)
        except Exception:
            logger.debug("Failed to persist policy profile", exc_info=True)

        if profile == _POLICY_STRICT:
            self.verify_ssl_check.setChecked(True)
            self.follow_redirects_check.setChecked(False)
            self._policy_hint.setText(
                "Strict: blocks insecure HTTP, enforces SSL verification, disables scripts, and warns on redirects."
            )
        elif profile == _POLICY_PERMISSIVE:
            self._policy_hint.setText(
                "Permissive: allows advanced flows with fewer preflight guardrails. Use for trusted test environments only."
            )
        else:
            self.verify_ssl_check.setChecked(True)
            self.follow_redirects_check.setChecked(True)
            self._policy_hint.setText(
                "Balanced: secure defaults with practical flexibility for day-to-day API testing."
            )

    def get_policy_profile(self) -> str:
        """Return currently selected request-policy profile."""
        return str(getattr(self, "_policy_profile", _POLICY_BALANCED))

    def _browse_file_to_input(self, title: str, filters: str, target: QLineEdit) -> None:
        """Open a file-picker dialog and write the chosen path into *target* QLineEdit."""
        path, _ = QFileDialog.getOpenFileName(self, title, "", filters)
        if path:
            target.setText(path)
            logger.debug("File selected via '%s'", title)

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
        from equinox.core.curl_parser import parse_curl

        logger.debug("cURL import dialog opened")

        # Pre-fill with clipboard contents if it looks like a curl command
        clipboard_text = QApplication.clipboard().text().strip()
        prefill = clipboard_text if clipboard_text.lower().startswith("curl ") else ""

        text, ok = QInputDialog.getMultiLineText(
            self, "Import from cURL", "Paste a cURL command:", prefill
        )
        if not ok or not text.strip():
            logger.debug("cURL import cancelled by user")
            return

        try:
            parsed = parse_curl(text.strip())
        except Exception as exc:
            logger.warning("Failed to parse cURL command (len=%d): %s", len(text), exc)
            QMessageBox.warning(self, "Parse Error", f"Could not parse cURL command:\n{exc}")
            return

        # Populate the editor
        method = parsed.get("method", "GET")
        url = parsed.get("url", "")
        headers = parsed.get("headers") or {}
        body = parsed.get("body")

        idx = self.method_combo.findText(method)
        if idx >= 0:
            self.method_combo.setCurrentIndex(idx)
        self.url_input.setText(url)
        self.headers_table.set_data(headers)
        if body is not None:
            body_text = body if isinstance(body, str) else str(body)
            self.body_text.setPlainText(body_text)
            self.body_type_combo.setCurrentText(self._detect_body_type(body_text, headers))
        else:
            self.body_text.clear()
            self.body_type_combo.setCurrentIndex(0)
        if not parsed.get("verify_ssl", True):
            self.verify_ssl_check.setChecked(False)

        self._mark_dirty()
        self._status_message("Request imported from cURL command")
        logger.info("cURL import: %s %s (%d headers)", method, url, len(headers))

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
        body = (
            self.body_text.toPlainText().strip() or None
            if body_type not in ("none", "multipart/form-data", "GraphQL")
            else None
        )
        req = Request(
            method=method, url=url, headers=headers, body=body,
            timeout=self.timeout_spin.value(),
            verify_ssl=self.verify_ssl_check.isChecked(),
            follow_redirects=self.follow_redirects_check.isChecked(),
        )
        logger.debug("Opening benchmark: %s %s", method, url)
        try:
            BenchmarkDialog(req, self, cookie_manager=self._cookie_manager).exec()
        except Exception:
            logger.error("Failed to open benchmark dialog", exc_info=True)

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
        """Append a header preset row, or navigate to the existing row if the key is present."""
        for row in range(self.headers_table.rowCount()):
            key_item = self.headers_table.item(row, 1)
            if key_item and key_item.text().strip().lower() == key.lower():
                # Key already present — select its value cell so the user can edit it.
                self.headers_table.setCurrentCell(row, 2)
                return
        self.headers_table.add_row(key, value)
        self._mark_dirty()
        self._update_tab_labels()

    def _add_row_and_focus(self, table: CheckableKeyValueTable) -> None:
        """Append an empty row to *table*, select its key cell, and mark dirty."""
        table.add_row("", "", enabled=True)
        # -2 skips the trailing empty sentinel row that CheckableKeyValueTable
        # always maintains; after add_row() there is always ≥1 real data row.
        last = table.rowCount() - 2  # last real (non-sentinel) row index
        if last >= 0:
            table.setCurrentCell(last, 1)  # column 1 = Key
            item = table.item(last, 1)
            if item:
                table.editItem(item)
        self._mark_dirty()
        self._update_tab_labels()

    def _remove_table_rows(self, table) -> None:
        rows_to_remove = sorted({idx.row() for idx in table.selectedIndexes()}, reverse=True)
        for r in rows_to_remove:
            table.removeRow(r)
        self._mark_dirty()
        self._update_tab_labels()

    # ── Per-table convenience wrappers (used by toolbar slots and tests) ──

    def _headers_add_row(self) -> None:
        self._add_row_and_focus(self.headers_table)

    def _headers_remove_row(self) -> None:
        self._remove_table_rows(self.headers_table)

    def _params_add_row(self) -> None:
        self._add_row_and_focus(self.params_table)

    def _params_remove_row(self) -> None:
        self._remove_table_rows(self.params_table)

    def _params_set_all(self, enabled: bool) -> None:
        """Enable or disable every row in the params table."""
        self._set_all_checkable(self.params_table, enabled)

    def _headers_set_all(self, enabled: bool) -> None:
        """Enable or disable every row in the headers table."""
        self._set_all_checkable(self.headers_table, enabled)

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
        try:
            self.path_params_table.update_from_url(text)
            visible = self.path_params_table.rowCount() > 0
            self._path_params_widget.setVisible(visible)
            self._update_tab_labels()
        except Exception:
            logger.debug("Failed to update path parameters from URL", exc_info=True)

    # ── Format JSON (#6) ──────────────────────────────────────────────

    def _format_json_body(self) -> None:
        """Pretty-print the JSON in the body editor."""
        text = self.body_text.toPlainText()
        if not text.strip():
            return
        t0 = time.perf_counter()
        try:
            formatted = json.dumps(json.loads(text), indent=2, ensure_ascii=False)
            self.body_text.setPlainText(formatted)
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            logger.info(
                "request_panel.json_formatted op=format_json_body original_length=%d formatted_length=%d elapsed_ms=%d",
                len(text),
                len(formatted),
                elapsed_ms,
            )
        except json.JSONDecodeError as exc:
            logger.warning("JSON formatting failed: %s (line %d, col %d)", exc.msg, exc.lineno, exc.colno)
            self._status_message(f"Invalid JSON: {exc}")

    def _save_request(self) -> None:
        """Save the current editor state to a collection (prompts for name / folder)."""
        url = self.url_input.text().strip()
        if not url:
            QMessageBox.warning(self, "Missing URL", "Please enter a URL before saving.")
            return

        method = self.method_combo.currentText()
        current_folder = getattr(self.current_request, "folder", None) or ""
        logger.debug(
            "request_panel.save_dialog_open op=save_request method=%s url=%s",
            method,
            url,
        )

        dlg = SaveRequestDialog(self.db, method, url, current_folder, parent=self)
        logger.debug("request_panel.save_dialog_created op=save_request")
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        name, col_id, col_name, folder = dlg.result_values()
        logger.debug(
            "request_panel.save_dialog_values op=save_request name=%s collection_id=%s folder=%s",
            name,
            col_id,
            folder,
        )

        try:
            existing_req = self.current_request
            existing_id = getattr(existing_req, "id", None)
            existing_collection_id = getattr(existing_req, "collection_id", None)
            request = self._build_request_from_editor(
                name=name,
                collection_id=col_id,
                folder=folder,
            )
            if existing_id and existing_collection_id == col_id:
                # Update in place when staying within the same collection.
                request.id = existing_id
                self._collection_mgr.update_request(request)
                req_id = existing_id
                logger.info(
                    "request_panel.request_updated op=save_request request_id=%d collection_id=%d method=%s url=%s",
                    req_id,
                    col_id,
                    method,
                    url,
                )
            else:
                # Save-as behavior for first save or cross-collection target.
                req_id = self._collection_mgr.save_request(request, collection_id=col_id, name=name)
                request.id = req_id
                logger.info(
                    "request_panel.request_saved op=save_request request_id=%d collection_id=%d method=%s url=%s",
                    req_id,
                    col_id,
                    method,
                    url,
                )

            self.current_request = request
            self._clear_dirty()

            self._status_message(f"Saved '{name}' to '{col_name}'")

            try:
                win = self.window()
                if hasattr(win, 'collections_panel'):
                    win.collections_panel.refresh()
            except Exception:
                logger.debug("Failed to refresh collections panel after save", exc_info=True)

        except Exception as exc:
            logger.error("Failed to save request", exc_info=True)
            QMessageBox.critical(self, "Save Failed", str(exc))

