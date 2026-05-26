"""Request builder panel.

Logging strategy:
- Entry/exit: major operations (load_request, send_request, save_request)
- Context: method, URL, request_id for structured logs
- Errors: full exception context with request details
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, NamedTuple, cast

from PyQt6.QtCore import QStringListModel, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QCompleter,
    QHBoxLayout,
    QLabel,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from equinox.application.requests import (
    RequestEditorSnapshot,
    RequestHistoryService,
    RequestPersistenceFacade,
)
from equinox.core.http.cookies import CookieManager
from equinox.core.request import Request
from equinox.core.validation import Validator
from equinox.gui.dialogs.save_dialog import SaveRequestDialog  # noqa: F401
from equinox.gui.request_panel._constants import (
    _HEADER_PRESETS,
    _KEY_POLICY_PROFILE,
    _POLICY_BALANCED,
    _POLICY_PERMISSIVE,
    _POLICY_STRICT,
    _SCRIPTS_CHEAT_TEXT,
    BROWSE_BTN_WIDTH,
    COMPLETER_MAX_VISIBLE,
    HISTORY_COMPLETER_LIMIT,
)
from equinox.gui.request_panel._mixins import _RequestAuthMixin, _RequestSendMixin
from equinox.gui.request_panel._mixins.autosave_mixin import RequestAutosaveMixin
from equinox.gui.request_panel._mixins.body_mixin import RequestBodyMixin
from equinox.gui.request_panel._mixins.bottom_bar_mixin import BottomBarMixin
from equinox.gui.request_panel._mixins.commands_mixin import RequestCommandsMixin
from equinox.gui.request_panel._mixins.dirty_tracking_mixin import DirtyTrackingMixin
from equinox.gui.request_panel._mixins.save_flow_mixin import RequestSaveFlowMixin
from equinox.gui.request_panel._mixins.scripts_tab_builder import create_scripts_tab
from equinox.gui.request_panel._mixins.settings_tab_builder import SettingsTabMixin
from equinox.gui.request_panel._mixins.ui_builder_mixins import (
    BodyEditorMixin,
    BodySearchBarMixin,
    BodyTabMixin,
    BodyTypeBarMixin,
    GraphQLMixin,
    MultipartMixin,
    NotesTabMixin,
    RequestPanelOrchestrationMixin,
    URLBarMixin,
)
from equinox.gui.request_panel._mixins.validation_mixin import _RequestValidationMixin
from equinox.gui.request_panel.toolbar import TabToolbar
from equinox.gui.widgets import CheckableKeyValueTable, PathParamsTable
from equinox.gui.workers import (  # noqa: F401 (RequestWorker used as type annotation)
    DEFAULT_TIMEOUT,
    RequestWorker,
)
from equinox.storage import Database

from ..ui_common import get_gui_settings

__all__ = ["RequestPanel"]


logger = logging.getLogger(__name__)


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
    DirtyTrackingMixin,
    SettingsTabMixin,
    BottomBarMixin,
    RequestPanelOrchestrationMixin,
    URLBarMixin,
    BodyTabMixin,
    BodyTypeBarMixin,
    BodySearchBarMixin,
    BodyEditorMixin,
    MultipartMixin,
    GraphQLMixin,
    NotesTabMixin,
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
        self.logger = logger
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

    def _setup_dirty_tracking(self) -> None:
        """Backward-compatible wrapper around the extracted dirty-tracking mixin."""
        self.setup_dirty_tracking()

    def _build_bottom_bar(self) -> QHBoxLayout:
        """Backward-compatible wrapper around bottom-bar mixin API."""
        return self.build_bottom_bar()

    def _create_settings_tab(self) -> QWidget:
        """Backward-compatible wrapper around settings-tab builder mixin API."""
        return self.create_settings_tab(
            default_timeout=DEFAULT_TIMEOUT,
            browse_button_width=BROWSE_BTN_WIDTH,
            policy_options=(_POLICY_STRICT, _POLICY_BALANCED, _POLICY_PERMISSIVE),
        )

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
                raw = to_dict()
                if not isinstance(raw, dict):
                    return auth_type, {}
                return auth_type, cast(dict[str, Any], dict(raw))
            except Exception:
                logger.debug("Failed to serialise auth state for snapshot", exc_info=True)
                return auth_type, {}

        auth_type, auth_data = _serialize_auth(self._auth)
        inherited_auth_type, inherited_auth_data = _serialize_auth(self._inherited_auth)
        headers = cast(dict[str, str], dict(self.headers_table.get_data()))
        params = cast(dict[str, str], dict(self.params_table.get_enabled_data()))
        params_list = cast(
            tuple[dict[str, Any], ...],
            tuple(dict(row) for row in self.params_table.get_all_rows()),
        )
        path_params = cast(dict[str, str], dict(self.path_params_table.get_all_data()))
        multipart_data = cast(
            tuple[dict[str, Any], ...],
            tuple(dict(row) for row in self._get_multipart_data()),
        )
        captures = cast(
            tuple[dict[str, Any], ...], tuple(dict(rule) for rule in self._get_captures())
        )
        assertions = cast(
            tuple[dict[str, Any], ...],
            tuple(dict(rule) for rule in self._get_assertions()),
        )
        return RequestEditorSnapshot(
            method=self.method_combo.currentText(),
            url=self.url_input.text().strip(),
            headers=headers,
            params=params,
            params_list=params_list,
            body=self.body_text.toPlainText(),
            body_type=self.body_type_combo.currentText(),
            graphql_query=self._gql_query.toPlainText(),
            graphql_variables=self._gql_vars.toPlainText(),
            multipart_data=multipart_data,
            path_params=path_params,
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
            captures=captures,
            assertions=assertions,
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
        self.build_request_panel_ui()

    def _configure_tab_metadata(self) -> None:
        self.configure_tab_metadata()

    # ── UI sub-builders (called once from _init_ui) ────────────────────

    def _build_url_bar(self) -> QHBoxLayout:
        return self.build_url_bar()

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
        return self.build_body_tab()

    def _build_body_type_bar(self) -> QHBoxLayout:
        return self.build_body_type_bar()

    def _build_body_search_bar(self) -> QHBoxLayout:
        return self.build_body_search_bar()

    def _build_body_editor(self, layout: QVBoxLayout) -> None:
        self.build_body_editor(layout)

    def _build_multipart_section(self, layout: QVBoxLayout) -> None:
        self.build_multipart_section(layout)

    def _build_graphql_section(self, layout: QVBoxLayout) -> None:
        self.build_graphql_section(layout)

    def _build_notes_tab(self) -> QWidget:
        return self.build_notes_tab()

    def _create_scripts_tab(self) -> QWidget:
        return create_scripts_tab(self, _SCRIPTS_CHEAT_TEXT)

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
            url_input = cast(Any, self.url_input)
            enabled = self.params_table.get_enabled_data()
            if not enabled:
                url_input.set_param_suffix("")
                return
            sep = "&" if "?" in url_input.text() else "?"
            parts = [f"{k}={v}" for k, v in enabled.items() if k]
            url_input.set_param_suffix(sep + "&".join(parts))
        except Exception:
            logger.debug("Failed to update URL suffix", exc_info=True)
            cast(Any, self.url_input).set_param_suffix("")

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
            parsed = json.loads(text)
            formatted = json.dumps(parsed, indent=2, ensure_ascii=False)
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
