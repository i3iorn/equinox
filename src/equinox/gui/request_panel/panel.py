"""Request builder panel.

Logging strategy:
- Entry/exit: major operations (load_request, send_request, save_request)
- Context: method, URL, request_id for structured logs
- Errors: full exception context with request details
"""

import json
import logging
import time
from typing import Any, NamedTuple, Callable

from PyQt6.QtCore import QStringListModel, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QCompleter,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from equinox.application.requests import (
    RequestEditorSnapshot,
    RequestHistoryService,
    RequestPersistenceFacade,
)
from equinox.core.format.error_enrichment import (  # noqa: F401 (used in mixin layer)
    RichError,
    enrich_exception,
)
from equinox.core.http.cookies import CookieManager
from equinox.core.request import Request
from equinox.core.validation import Validator
from equinox.gui.request_panel._constants import (
    BROWSE_BTN_WIDTH,
    CANCEL_BTN_WIDTH,
    COMPLETER_MAX_VISIBLE,
    FMT_JSON_BTN_WIDTH,
    HISTORY_COMPLETER_LIMIT,
    METHOD_COMBO_WIDTH,
    SEND_BTN_WIDTH,
)
from equinox.gui.request_panel.autosave_mixin import RequestAutosaveMixin
from equinox.gui.request_panel.body_mixin import RequestBodyMixin  # noqa: F401
from equinox.gui.request_panel.body_text_proxy import BodyTextProxy
from equinox.gui.request_panel.commands_mixin import RequestCommandsMixin
from equinox.gui.request_panel.mixins import (  # noqa: F401
    _RequestAuthMixin,
    _RequestSendMixin,
)
from equinox.gui.request_panel.save_dialog import SaveRequestDialog  # noqa: F401
from equinox.gui.request_panel.save_flow_mixin import RequestSaveFlowMixin
from equinox.gui.request_panel.toolbar import TabToolbar
from equinox.gui.request_panel.validation_mixin import _RequestValidationMixin  # noqa: F401
from equinox.gui.syntax_highlighter.python_highlighter import PythonHighlighter
from equinox.gui.theme import get_mono_font
from equinox.gui.widgets import CheckableKeyValueTable, JsonBodyEditor, PathParamsTable, UrlLineEdit
from equinox.gui.workers import (  # noqa: F401 (RequestWorker used as type annotation)
    DEFAULT_TIMEOUT,
    RequestWorker,
)
from equinox.storage import Database

from ..ui_common import configure_tab_persistence, get_gui_settings

logger = logging.getLogger(__name__)
_KEY_POLICY_PROFILE = "request/policy_profile"
_KEY_ACTIVE_TAB = "request/active_tab"
_POLICY_STRICT = "Strict"
_POLICY_BALANCED = "Balanced"
_POLICY_PERMISSIVE = "Permissive"


__all__ = ["RequestPanel"]

# Common header presets for the Headers tab toolbar
_HEADER_PRESETS = [
    ("Content-Type: application/json", "Content-Type", "application/json"),
    ("Content-Type: application/xml", "Content-Type", "application/xml"),
    (
        "Content-Type: application/x-www-form-urlencoded",
        "Content-Type",
        "application/x-www-form-urlencoded",
    ),
    ("Content-Type: multipart/form-data", "Content-Type", "multipart/form-data"),
    ("Content-Type: text/plain", "Content-Type", "text/plain"),
    None,
    ("Accept: application/json", "Accept", "application/json"),
    ("Accept: application/xml", "Accept", "application/xml"),
    ("Accept: */*", "Accept", "*/*"),
    None,
    ("Authorization: Bearer …", "Authorization", "Bearer "),
    ("X-API-Key: …", "X-API-Key", ""),
    None,
    ("Cache-Control: no-cache", "Cache-Control", "no-cache"),
    ("User-Agent: Equinox/1.0", "User-Agent", "Equinox/1.0"),
]

# HTML cheat-sheet shown in the collapsible Scripts tab section
_SCRIPTS_CHEAT_TEXT = (
    "<h3>Pre/Post Scripts</h3>" "<p>Use Python helpers to mutate the request/response context.</p>"
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


class RequestPanel(
    RequestAutosaveMixin,
    RequestSaveFlowMixin,
    RequestCommandsMixin,
    _RequestValidationMixin,
    _RequestSendMixin,
    _RequestAuthMixin,
    RequestBodyMixin,
    QWidget,
):
    """Panel for building and sending HTTP requests."""

    response_received = pyqtSignal(object)
    request_sent = pyqtSignal(object)
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

    def __init__(
        self,
        db: Database,
        parent=None,
        cookie_manager: CookieManager | None = None,
        request_persistence: RequestPersistenceFacade | None = None,
        request_history: RequestHistoryService | None = None,
    ):
        super().__init__(parent)
        logger.debug("RequestPanel.__init__ starting")
        self.db = db
        self._cookie_manager: CookieManager | None = cookie_manager

        self._request_persistence: RequestPersistenceFacade = (
            request_persistence or RequestPersistenceFacade(db)
        )
        self._request_history: RequestHistoryService = request_history or RequestHistoryService(db)
        self.current_request: Request | None = None
        self._auth = None
        self._inherited_auth = None
        self._inherited_auth_source = None
        self._session_vars: dict[str, str] = {}
        self._worker: RequestWorker | None = None
        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.setInterval(100)
        self._elapsed_timer.timeout.connect(self._tick_elapsed)
        self._elapsed_secs = 0.0
        self._dirty = False
        self._settings = get_gui_settings()
        self._policy_profile = str(self._settings.value(_KEY_POLICY_PROFILE, _POLICY_BALANCED))

        self._init_ui()
        self._setup_dirty_tracking()
        self._setup_url_completer()
        self._init_validation()  # Initialize real-time validation
        self._setup_shortcuts()

        logger.info("RequestPanel initialized successfully")

    # Dirty-state and autosave behavior moved to RequestAutosaveMixin.

    # ── Session variable accessors ─────────────────────────────────────

    def get_session_vars(self) -> dict[str, str]:
        """Return a copy of the current session variables."""
        return dict(self._session_vars)

    def get_interpolation_context(self) -> dict[str, str]:
        """Return the current interpolation snapshot for helper panels."""
        context = self.get_session_vars()
        try:
            context.update(self.path_params_table.get_all_data())
        except Exception:
            logger.debug("Failed to read path parameters for interpolation context", exc_info=True)
        return context

    def set_session_var(self, key: str, value: str) -> None:
        """Set a captured session variable and notify listeners."""
        validated_key = Validator.validate_variable_name(key)
        self._session_vars[validated_key] = value
        self.session_vars_changed.emit(dict(self._session_vars))

    def delete_session_var(self, key: str) -> bool:
        """Delete a session variable by key; returns False if it was absent."""
        validated_key = Validator.validate_variable_name(key)
        if validated_key not in self._session_vars:
            return False
        del self._session_vars[validated_key]
        self.session_vars_changed.emit(dict(self._session_vars))
        return True

    def clear_session_vars(self) -> None:
        """Clear all session variables and notify listeners."""
        self._session_vars.clear()
        self.session_vars_changed.emit({})

    def _build_request_editor_snapshot(self) -> RequestEditorSnapshot:
        """Capture the current request-editor widget state as plain data."""
        request = self.current_request

        def _serialize_auth(value: Any) -> tuple[str | None, dict[str, Any]]:
            if value is None:
                return None, {}
            auth_type = type(value).__name__
            to_dict = getattr(value, "to_dict", None)
            if not callable(to_dict):
                return auth_type, {}
            try:
                return auth_type, dict(to_dict())
            except Exception:
                logger.debug("Failed to serialise auth state for snapshot", exc_info=True)
                return auth_type, {}

        auth_type, auth_data = _serialize_auth(self._auth)
        inherited_auth_type, inherited_auth_data = _serialize_auth(self._inherited_auth)
        return RequestEditorSnapshot(
            method=self.method_combo.currentText(),
            url=self.url_input.text().strip(),
            headers=dict(self.headers_table.get_data()),
            params=dict(self.params_table.get_enabled_data()),
            params_list=tuple(dict(row) for row in self.params_table.get_all_rows()),
            body=self.body_text.toPlainText(),
            body_type=self.body_type_combo.currentText(),
            graphql_query=self._gql_query.toPlainText(),
            graphql_variables=self._gql_vars.toPlainText(),
            multipart_data=tuple(dict(row) for row in self._get_multipart_data()),
            path_params=dict(self.path_params_table.get_all_data()),
            timeout=float(self.timeout_spin.value()),
            verify_ssl=bool(self.verify_ssl_check.isChecked()),
            follow_redirects=bool(self.follow_redirects_check.isChecked()),
            name=getattr(request, "name", None),
            description=self.notes_editor.toPlainText().strip() or None,
            collection_id=getattr(request, "collection_id", None),
            folder=getattr(request, "folder", None),
            request_id=getattr(request, "id", None),
            auth_type=auth_type,
            auth_data=auth_data,
            inherited_auth_type=inherited_auth_type,
            inherited_auth_data=inherited_auth_data,
            inherited_auth_source=self._inherited_auth_source,
            captures=tuple(dict(rule) for rule in self._get_captures()),
            assertions=tuple(dict(rule) for rule in self._get_assertions()),
            pre_script=self.pre_script_editor.toPlainText(),
            post_script=self.post_script_editor.toPlainText(),
            cert_path=self.cert_path_input.text().strip() or None,
            cert_key_path=self.cert_key_input.text().strip() or None,
            session_vars=dict(self._session_vars),
        )

    def _clear_dirty(self) -> None:
        self._dirty = False
        self._sync_editor_state_ui()

    def _sync_editor_state_ui(self) -> None:
        """Reflect scratch/saved/dirty state in the request footer."""
        save_button = getattr(self, "save_button", None)
        state_label = getattr(self, "_editor_state_label", None)
        has_saved_target = bool(getattr(self.current_request, "id", None))

        if self._dirty:
            if save_button is not None:
                save_button.setText("Save Changes")
                save_button.setToolTip("Save the current request changes to a collection")
            if state_label is not None:
                state_label.setText("Unsaved changes")
            return

        if save_button is not None:
            save_button.setText("Save")
            save_button.setToolTip("Save to a collection (prompts for name / folder)")

        if state_label is None:
            return
        if has_saved_target:
            state_label.setText("Saved to collection")
        else:
            state_label.setText("Scratch request")

    def send(self) -> None:
        """Public wrapper for sending the current request.

        External callers (e.g. other GUI panels) should call this instead of
        invoking the private ``_send_request`` method directly.
        """
        self._send_request()

    def _setup_dirty_tracking(self) -> None:
        """Connect change signals on all editor widgets to mark dirty."""
        _connected = 0

        def safe_connect(get_signal: Callable[[], pyqtSignal], slot: Callable, name=None):
            """Lazily retrieve a signal via get_signal() and connect it to slot."""
            nonlocal _connected
            try:
                sig = get_signal()
            except (AttributeError, RuntimeError) as exc:
                logger.debug(
                    "Signal retrieval skipped (C++ object missing): %s — %s",
                    name,
                    type(exc).__name__,
                )
                return
            except Exception:
                logger.warning("Unexpected error retrieving signal: %s", name, exc_info=True)
                return
            try:
                sig.connect(slot)
                _connected += 1
            except RuntimeError:
                logger.debug("Failed to connect signal after retrieval: %s", name)

        safe_connect(lambda: self.url_input.textChanged, self._mark_dirty, "url_input.textChanged")
        safe_connect(
            lambda: self.method_combo.currentIndexChanged,
            self._mark_dirty,
            "method_combo.currentIndexChanged",
        )
        safe_connect(lambda: self.body_text.textChanged, self._mark_dirty, "body_text.textChanged")
        safe_connect(
            lambda: self.body_text.textChanged,
            self._update_tab_labels,
            "body_text.textChanged->update_tab_labels",
        )
        safe_connect(
            lambda: self.body_type_combo.currentIndexChanged,
            self._mark_dirty,
            "body_type_combo.currentIndexChanged",
        )
        safe_connect(
            lambda: self.headers_table.itemChanged, self._mark_dirty, "headers_table.itemChanged"
        )
        safe_connect(
            lambda: self.headers_table.itemChanged,
            self._update_tab_labels,
            "headers_table.itemChanged->update_tab_labels",
        )
        safe_connect(
            lambda: self.params_table.itemChanged, self._mark_dirty, "params_table.itemChanged"
        )
        safe_connect(
            lambda: self.params_table.itemChanged,
            self._update_tab_labels,
            "params_table.itemChanged->update_tab_labels",
        )
        safe_connect(
            lambda: self.params_table.itemChanged,
            self._update_url_suffix,
            "params_table.itemChanged->update_url_suffix",
        )
        safe_connect(
            lambda: self.url_input.textChanged,
            self._update_url_suffix,
            "url_input.textChanged->update_url_suffix",
        )
        safe_connect(
            lambda: self.path_params_table.paramsChanged,
            self._mark_dirty,
            "path_params_table.paramsChanged",
        )
        safe_connect(
            lambda: self._multipart_table.itemChanged,
            self._mark_dirty,
            "_multipart_table.itemChanged",
        )
        safe_connect(
            lambda: self._multipart_table.itemChanged,
            self._update_tab_labels,
            "_multipart_table.itemChanged->update_tab_labels",
        )
        safe_connect(
            lambda: self.timeout_spin.valueChanged, self._mark_dirty, "timeout_spin.valueChanged"
        )
        safe_connect(
            lambda: self.verify_ssl_check.stateChanged,
            self._mark_dirty,
            "verify_ssl_check.stateChanged",
        )
        safe_connect(
            lambda: self.verify_ssl_check.stateChanged,
            lambda: self._update_auth_display(self._auth),
            "verify_ssl_check.stateChanged->auth_display",
        )
        safe_connect(
            lambda: self.follow_redirects_check.stateChanged,
            self._mark_dirty,
            "follow_redirects_check.stateChanged",
        )
        safe_connect(
            lambda: self.url_input.textChanged,
            lambda: self._update_auth_display(self._auth),
            "url_input.textChanged->auth_display",
        )
        safe_connect(
            lambda: self.notes_editor.textChanged, self._mark_dirty, "notes_editor.textChanged"
        )
        safe_connect(
            lambda: self._gql_query.textChanged, self._mark_dirty, "_gql_query.textChanged"
        )
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
            self._url_values = self._request_history.list_recent_urls(limit=HISTORY_COMPLETER_LIMIT)
            self._known_urls = set(self._url_values)
            self._url_model.setStringList(self._url_values)
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            logger.debug(
                "request_panel.url_completer_refreshed op=refresh_url_completer history_entries=%d url_count=%d elapsed_ms=%d",
                len(self._url_values),
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
        self.tabs.setObjectName("requestTabs")
        self.tabs.addTab(self._build_headers_tab(), "Headers")
        self.tabs.addTab(self._build_params_tab(), "Params")
        self.tabs.addTab(self._build_body_tab(), "Body")
        self.tabs.addTab(self._create_auth_tab(), "Auth")  # defined in _RequestAuthMixin
        self.tabs.addTab(self._create_captures_tab(), "Captures")  # defined in RequestBodyMixin
        self.tabs.addTab(self._create_assertions_tab(), "Assertions")  # defined in RequestBodyMixin
        self.tabs.addTab(self._create_scripts_tab(), "Scripts")
        self.tabs.addTab(self._create_settings_tab(), "Settings")
        self.tabs.addTab(self._build_notes_tab(), "Notes")
        self._configure_tab_metadata()
        configure_tab_persistence(
            self.tabs,
            settings_key=_KEY_ACTIVE_TAB,
            default_tab="Headers",
            settings=self._settings,
        )
        layout.addWidget(self.tabs, 1)

        bottom_container = QWidget()
        bottom_layout = QVBoxLayout(bottom_container)
        bottom_layout.setContentsMargins(6, 0, 6, 6)
        bottom_layout.addLayout(self._build_bottom_bar())
        layout.addWidget(bottom_container)
        self._sync_editor_state_ui()

    def _configure_tab_metadata(self) -> None:
        """Attach stable tooltips to request tabs for faster discovery."""
        tab_tooltips = {
            "Headers": "Request headers sent with the call",
            "Params": "Query-string and path parameters",
            "Body": "Request payload, multipart form data, or GraphQL body",
            "Auth": "Per-request authentication configuration",
            "Captures": "Extract response values into session variables",
            "Assertions": "Verify status, headers, body, and timing rules",
            "Scripts": "Pre-request and post-response Python scripts",
            "Settings": "Timeouts, TLS, redirects, and client certificate options",
            "Notes": "Request documentation, examples, and working notes",
        }
        for index in range(self.tabs.count()):
            label = self.tabs.tabText(index)
            tooltip = tab_tooltips.get(label)
            if tooltip:
                self.tabs.setTabToolTip(index, tooltip)

    # ── UI sub-builders (called once from _init_ui) ────────────────────

    def _build_url_bar(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(4)
        self.method_combo = QComboBox()
        self.method_combo.setObjectName("requestMethodCombo")
        self.method_combo.setProperty("usage_track_id", "request.method_combo")
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
        self.send_button.setProperty("usage_track_id", "request.send")
        self.send_button.setMinimumWidth(SEND_BTN_WIDTH)
        self.send_button.setToolTip("Send request (Ctrl+Enter)")
        self.send_button.clicked.connect(self._send_request)
        self.send_button.setDefault(True)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setObjectName("cancelBtn")
        self.cancel_button.setProperty("usage_track_id", "request.cancel")
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

    def _set_url_fix_suggestion(self, suggestion: str | None, reason: str = "") -> None:
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

        self.save_button = QPushButton("Save")
        self.save_button.setObjectName("requestSaveBtn")
        self.save_button.setProperty("usage_track_id", "request.save")
        self.save_button.setMinimumWidth(SEND_BTN_WIDTH)
        self.save_button.setToolTip("Save to a collection (prompts for name / folder)")
        self.save_button.clicked.connect(self._save_request)

        more_btn = QToolButton()
        more_btn.setText("More ▾")
        more_btn.setToolTip("Secondary request tools")
        more_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        more_btn.setObjectName("requestMoreToolsBtn")
        more_btn.setProperty("usage_track_id", "request.more_tools")

        more_menu = QMenu(more_btn)
        import_action = QAction("Import from cURL…", self)
        import_action.setObjectName("request_import_curl")
        import_action.setProperty("usage_track_id", "request.import_curl")
        import_action.triggered.connect(self._import_from_curl)

        benchmark_action = QAction("Benchmark…", self)
        benchmark_action.setObjectName("request_benchmark")
        benchmark_action.setProperty("usage_track_id", "request.benchmark")
        benchmark_action.triggered.connect(self._open_benchmark)

        clear_session_action = QAction("Clear Session Vars", self)
        clear_session_action.setObjectName("request_clear_session_vars")
        clear_session_action.setProperty("usage_track_id", "request.clear_session_vars")
        clear_session_action.triggered.connect(self.clear_session_vars)

        self._secondary_tool_actions = [
            (import_action, False),
            (benchmark_action, False),
            (clear_session_action, True),
        ]
        self._secondary_tools_menu = more_menu
        self._rebuild_secondary_tools_menu()
        more_menu.aboutToShow.connect(self._rebuild_secondary_tools_menu)

        more_btn.setMenu(more_menu)

        self._session_vars_label = QLabel("Session vars: 0")
        self._session_vars_label.setObjectName("mutedLabel")
        self._editor_state_label = QLabel("Scratch request")
        self._editor_state_label.setObjectName("mutedLabel")

        row.addWidget(self.save_button)
        row.addWidget(more_btn)
        row.addStretch()
        row.addWidget(self._editor_state_label)
        row.addWidget(self._session_vars_label)

        # Update session-vars count whenever the signal is emitted
        self.session_vars_changed.connect(
            lambda d: self._session_vars_label.setText(f"Session vars: {len(d)}")
        )

        return row

    def _usage_count_for_action(self, action: QAction) -> int:
        tracker = getattr(self.window(), "_ui_usage_tracker", None)
        if tracker is None:
            return 0
        element_id = action.property("usage_track_id")
        if not isinstance(element_id, str) or not element_id.strip():
            return 0
        try:
            return tracker.get_count(
                category="action",
                context="panel_action",
                element_id=element_id,
            )
        except Exception:
            logger.debug("Failed to read usage count for action '%s'", element_id, exc_info=True)
            return 0

    def _rebuild_secondary_tools_menu(self) -> None:
        """Reorder secondary tools by usage while keeping destructive actions last."""
        if not hasattr(self, "_secondary_tools_menu"):
            return
        menu = self._secondary_tools_menu
        menu.clear()
        actions = list(getattr(self, "_secondary_tool_actions", []))
        if not actions:
            return

        ranked: list = []
        destructive: list = []
        for idx, (action, is_destructive) in enumerate(actions):
            if is_destructive:
                destructive.append((idx, action))
                continue
            ranked.append((self._usage_count_for_action(action), idx, action))

        ranked.sort(key=lambda item: (-item[0], item[1]))
        for _, _, action in ranked:
            menu.addAction(action)

        if destructive:
            menu.addSeparator()
            for _, action in sorted(destructive, key=lambda item: item[0]):
                menu.addAction(action)

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
        if presets:
            toolbar = TabToolbar(
                "",
                presets=presets,
                preset_context=f"request_{title}",
                parent=self,
            )
        else:
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
        result = self._build_kv_tab("Headers", presets=_HEADER_PRESETS, enable_key_completer=True)
        self.headers_table = result.table
        self._headers_toolbar = result.toolbar
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
            [
                "none",
                "raw (JSON)",
                "raw (XML)",
                "raw (text)",
                "form-urlencoded",
                "multipart/form-data",
                "GraphQL",
            ]
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

    def _build_script_section(
        self, title: str, placeholder: str
    ) -> tuple[QGroupBox, QPlainTextEdit, QLabel]:
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

        pre_group, self.pre_script_editor, self.pre_script_result = self._build_script_section(
            "Pre-request Script",
            "# Runs before the request is sent\n"
            "# Available: request (dict), env (dict)\n"
            "# Example: env['timestamp'] = str(int(__import__('time').time()))",
        )
        splitter.addWidget(pre_group)

        post_group, self.post_script_editor, self.post_script_result = self._build_script_section(
            "Post-response Script",
            "# Runs after response is received\n"
            "# Available: response (dict with status_code, headers, body, json), env (dict)\n"
            "# Example: env['user_id'] = str(response['json']['id'])",
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
                cheat_toggle.setText(
                    "▼ Available variables & modules"
                    if checked
                    else "▶ Available variables & modules"
                ),
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
        self.policy_profile_combo.addItems(
            [
                _POLICY_STRICT,
                _POLICY_BALANCED,
                _POLICY_PERMISSIVE,
            ]
        )
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
            logger.warning(
                "JSON formatting failed: %s (line %d, col %d)", exc.msg, exc.lineno, exc.colno
            )
            self._status_message(f"Invalid JSON: {exc}")
