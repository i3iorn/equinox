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
from collections.abc import Sequence
from typing import Any
from typing import cast
from typing import NamedTuple

from equinox.application.requests import RequestHistoryService
from equinox.application.requests import RequestPersistenceFacade
from equinox.core.http.cookies import CookieManager
from equinox.core.request import Request
from equinox.gui.dialogs.save_dialog import SaveRequestDialog  # noqa: F401
from equinox.gui.request_panel._constants import _HEADER_PRESETS
from equinox.gui.request_panel._constants import _KEY_POLICY_PROFILE
from equinox.gui.request_panel._constants import _POLICY_BALANCED
from equinox.gui.request_panel._constants import _POLICY_PERMISSIVE
from equinox.gui.request_panel._constants import _POLICY_STRICT
from equinox.gui.request_panel._constants import _SCRIPTS_CHEAT_TEXT
from equinox.gui.request_panel._constants import BROWSE_BTN_WIDTH
from equinox.gui.request_panel._mixins.auth_mixin import _RequestAuthMixin
from equinox.gui.request_panel._mixins.autosave_mixin import RequestAutosaveMixin
from equinox.gui.request_panel._mixins.body_mixin import RequestBodyMixin
from equinox.gui.request_panel._mixins.bottom_bar_mixin import BottomBarMixin
from equinox.gui.request_panel._mixins.commands_mixin import RequestCommandsMixin
from equinox.gui.request_panel._mixins.dirty_tracking_mixin import DirtyTrackingMixin
from equinox.gui.request_panel._mixins.request_editor_state_mixin import RequestEditorStateMixin
from equinox.gui.request_panel._mixins.request_tools_mixin import RequestToolsMixin
from equinox.gui.request_panel._mixins.save_flow_mixin import RequestSaveFlowMixin
from equinox.gui.request_panel._mixins.scripts_tab_builder import create_scripts_tab
from equinox.gui.request_panel._mixins.send_mixin import _RequestSendMixin
from equinox.gui.request_panel._mixins.settings_tab_builder import SettingsTabMixin
from equinox.gui.request_panel._mixins.ui_builder_mixins import BodyEditorMixin
from equinox.gui.request_panel._mixins.ui_builder_mixins import BodySearchBarMixin
from equinox.gui.request_panel._mixins.ui_builder_mixins import BodyTabMixin
from equinox.gui.request_panel._mixins.ui_builder_mixins import BodyTypeBarMixin
from equinox.gui.request_panel._mixins.ui_builder_mixins import GraphQLMixin
from equinox.gui.request_panel._mixins.ui_builder_mixins import MultipartMixin
from equinox.gui.request_panel._mixins.ui_builder_mixins import NotesTabMixin
from equinox.gui.request_panel._mixins.ui_builder_mixins import RequestPanelOrchestrationMixin
from equinox.gui.request_panel._mixins.ui_builder_mixins import URLBarMixin
from equinox.gui.request_panel._mixins.url_history_mixin import URLHistoryMixin
from equinox.gui.request_panel._mixins.validation_mixin import _RequestValidationMixin
from equinox.gui.widgets import CheckableKeyValueTable
from equinox.gui.widgets import PathParamsTable
from equinox.gui.widgets import TabToolbar
from equinox.gui.workers import DEFAULT_TIMEOUT
from equinox.gui.workers import RequestWorker
from equinox.storage import Database
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QHBoxLayout
from PyQt6.QtWidgets import QLabel
from PyQt6.QtWidgets import QToolButton
from PyQt6.QtWidgets import QVBoxLayout
from PyQt6.QtWidgets import QWidget

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
    RequestEditorStateMixin,  # type: ignore[misc]
    URLHistoryMixin,  # type: ignore[misc]
    RequestToolsMixin, # type: ignore[misc]
    RequestAutosaveMixin,  # type: ignore[misc]
    RequestSaveFlowMixin,  # type: ignore[misc]
    RequestCommandsMixin,  # type: ignore[misc]
    _RequestValidationMixin,
    _RequestSendMixin, # type: ignore[misc]
    _RequestAuthMixin, # type: ignore[misc]
    RequestBodyMixin, # type: ignore[misc]
    DirtyTrackingMixin,  # type: ignore[misc]
    SettingsTabMixin,  # type: ignore[misc]
    BottomBarMixin,
    RequestPanelOrchestrationMixin,  # type: ignore[misc]
    URLBarMixin,  # type: ignore[misc]
    BodyTabMixin,  # type: ignore[misc]
    BodyTypeBarMixin,  # type: ignore[misc]
    BodySearchBarMixin,  # type: ignore[misc]
    BodyEditorMixin,  # type: ignore[misc]
    MultipartMixin,  # type: ignore[misc]
    GraphQLMixin,  # type: ignore[misc]
    NotesTabMixin,  # type: ignore[misc]
    QWidget,
):
    """Panel for building and sending HTTP requests."""

    response_received = pyqtSignal(object)
    request_sent = pyqtSignal(object)
    session_vars_changed = pyqtSignal(dict)

    # ── Accessor helpers ───────────────────────────────────────────────

    @property
    def _logging_panel(self) -> Any | None:
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
        parent: QWidget | None = None,
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
        self._url_fix_suggestion: str | None = None
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
        return cast(
            QWidget, self.create_settings_tab(
                default_timeout=DEFAULT_TIMEOUT,
                browse_button_width=BROWSE_BTN_WIDTH,
                policy_options=(_POLICY_STRICT, _POLICY_BALANCED, _POLICY_PERMISSIVE),
            ),
        )

    def send(self) -> None:
        """Public wrapper for sending the current request.

        External callers (e.g. other GUI panels) should call this instead of
        invoking the private ``_send_request`` method directly.
        """
        self._send_request()

    # ── UI construction ───────────────────────────────────────────────

    def _init_ui(self) -> None:
        self.build_request_panel_ui()

    def _configure_tab_metadata(self) -> None:
        self.configure_tab_metadata()

    # ── UI sub-builders (called once from _init_ui) ────────────────────

    def _build_url_bar(self) -> QHBoxLayout:
        return cast(QHBoxLayout, self.build_url_bar())

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
        presets: Sequence[tuple[str, str, str] | None] | None = None,
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
        return cast(QWidget, self.build_body_tab())

    def _build_body_type_bar(self) -> QHBoxLayout:
        return cast(QHBoxLayout, self.build_body_type_bar())

    def _build_body_search_bar(self) -> QHBoxLayout:
        return cast(QHBoxLayout, self.build_body_search_bar())

    def _build_body_editor(self, layout: QVBoxLayout) -> None:
        self.build_body_editor(layout)

    def _build_multipart_section(self, layout: QVBoxLayout) -> None:
        self.build_multipart_section(layout)

    def _build_graphql_section(self, layout: QVBoxLayout) -> None:
        self.build_graphql_section(layout)

    def _build_notes_tab(self) -> QWidget:
        return cast(QWidget, self.build_notes_tab())

    def _create_scripts_tab(self) -> QWidget:
        return cast(QWidget, create_scripts_tab(self, _SCRIPTS_CHEAT_TEXT))

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
                "Strict: blocks insecure HTTP, enforces SSL verification, disables scripts, and warns on redirects.",
            )
        elif profile == _POLICY_PERMISSIVE:
            self._policy_hint.setText(
                "Permissive: allows advanced flows with fewer preflight guardrails. Use for trusted test environments only.",
            )
        else:
            self.verify_ssl_check.setChecked(True)
            self.follow_redirects_check.setChecked(True)
            self._policy_hint.setText(
                "Balanced: secure defaults with practical flexibility for day-to-day API testing.",
            )

    def get_policy_profile(self) -> str:
        """Return currently selected request-policy profile."""
        return str(getattr(self, "_policy_profile", _POLICY_BALANCED))

    # ── URL ghost-params preview ──────────────────────────────────────

    def _update_url_suffix(self, *_: object) -> None:
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
                "JSON formatting failed: %s (line %d, col %d)", exc.msg, exc.lineno, exc.colno,
            )
            self._status_message(f"Invalid JSON: {exc}")
