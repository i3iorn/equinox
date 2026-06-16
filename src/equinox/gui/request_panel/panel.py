"""Request builder panel.

Logging strategy:
- Entry/exit: major operations (load_request, send_request, save_request)
- Context: method, URL, request_id for structured logs
- Errors: full exception context with request details
"""
from __future__ import annotations

import logging

from equinox.application.requests import RequestHistoryService
from equinox.application.requests import RequestPersistenceFacade
from equinox.core.http.cookies import CookieManager
from equinox.core.request import Request
from equinox.gui.request_panel._mixins import _RequestAuthMixin
from equinox.gui.request_panel._mixins import _RequestSendMixin
from equinox.gui.request_panel._mixins.autosave_mixin import RequestAutosaveMixin
from equinox.gui.request_panel._mixins.body_mixin import RequestBodyMixin
from equinox.gui.request_panel._mixins.bottom_bar_mixin import BottomBarMixin
from equinox.gui.request_panel._mixins.commands_mixin import RequestCommandsMixin
from equinox.gui.request_panel._mixins.dirty_tracking_mixin import DirtyTrackingMixin
from equinox.gui.request_panel._mixins.panel_layout_mixin import RequestPanelLayoutMixin
from equinox.gui.request_panel._mixins.request_editor_state_mixin import RequestEditorStateMixin
from equinox.gui.request_panel._mixins.request_tools_mixin import RequestToolsMixin
from equinox.gui.request_panel._mixins.save_flow_mixin import RequestSaveFlowMixin
from equinox.gui.request_panel._mixins.settings_tab_builder import SettingsTabMixin
from equinox.gui.request_panel._mixins.ui_builder_mixins import RequestPanelOrchestrationMixin
from equinox.gui.request_panel._mixins.ui_builder_mixins import URLBarMixin
from equinox.gui.request_panel._mixins.url_history_mixin import URLHistoryMixin
from equinox.gui.request_panel._mixins.validation_mixin import _RequestValidationMixin
from equinox.gui.workers import RequestWorker
from equinox.storage import Database
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QWidget

from ..ui_common import get_gui_settings

logger = logging.getLogger(__name__)
_KEY_POLICY_PROFILE = "request/policy_profile"
_POLICY_STRICT = "Strict"
_POLICY_BALANCED = "Balanced"
_POLICY_PERMISSIVE = "Permissive"


__all__ = ["RequestPanel"]

# ─────────────────────────────────────────────────────────────────────────────
# Request panel
# ─────────────────────────────────────────────────────────────────────────────


class RequestPanel(
    RequestEditorStateMixin,
    URLHistoryMixin,
    RequestToolsMixin,
    BottomBarMixin,
    SettingsTabMixin,
    DirtyTrackingMixin,
    RequestAutosaveMixin,
    RequestSaveFlowMixin,
    RequestCommandsMixin,
    RequestPanelLayoutMixin,
    _RequestValidationMixin,
    _RequestSendMixin,
    _RequestAuthMixin,
    RequestBodyMixin,
    RequestPanelOrchestrationMixin,
    URLBarMixin,
    QWidget,
):
    """Panel for building and sending HTTP requests."""

    response_received = pyqtSignal(object)
    request_sent = pyqtSignal(object)
    session_vars_changed = pyqtSignal(dict)

    # ── Accessor helpers ───────────────────────────────────────────────

    @property
    def _logging_panel(self) -> QWidget | None:
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
    ) -> None:
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

    # Dirty-state and autosave behavior are provided by dedicated mixins.

    # Session-variable and editor-state helpers are provided by
    # RequestEditorStateMixin.

    def send(self) -> None:
        """Public wrapper for sending the current request.

        External callers (e.g. other GUI panels) should call this instead of
        invoking the private ``_send_request`` method directly.
        """
        self._send_request()
