"""Main window for Equinox GUI.

This module keeps high-level orchestration only.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from typing import cast

from equinox.application.history import HistoryFacade
from equinox.core.request import Request
from equinox.core.request import Response
from equinox.storage import Database
from equinox.storage.cookies import CookieJarManager
from PyQt6 import sip
from PyQt6.QtCore import QPoint
from PyQt6.QtCore import Qt
from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QKeySequence
from PyQt6.QtGui import QShortcut
from PyQt6.QtWidgets import QApplication
from PyQt6.QtWidgets import QHBoxLayout
from PyQt6.QtWidgets import QMainWindow
from PyQt6.QtWidgets import QSplitter
from PyQt6.QtWidgets import QTabWidget
from PyQt6.QtWidgets import QVBoxLayout
from PyQt6.QtWidgets import QWidget

from ..intelligence_worker import IntelligenceWorker
from ..logging_utils import log_gui_event
from ..ui_common import get_gui_settings
from ..ui_usage_tracker import UIUsageTracker
from ._environment import _EnvironmentMixin
from ._frameless import _FramelessMixin
from ._history import _HistoryMixin
from ._import_export import _ImportExportMixin
from ._layout import _LayoutMixin
from ._menu import _KEY_INTEL_DISABLED
from ._menu import _MenuMixin
from ._panels import _PanelsMixin

logger = logging.getLogger(__name__)


_WINDOW_X = 100
_WINDOW_Y = 100
_WINDOW_W = 1400
_WINDOW_H = 900
_LEFT_PANEL_W = 300
_RIGHT_PANEL_W = 1100
_REQ_PANEL_H = 400
_RESP_PANEL_H = 500
_MIN_REQ_H = 180
_MIN_RESP_H = 120
_MIN_LEFT_W = 180
_SPLITTER_HANDLE_W = 5
_STATUS_TIMEOUT_MS = 10_000
_TAB_HISTORY = 1
_TAB_COOKIES = 4
_LEFT_TAB_LABELS = ("Collections", "History", "Variables", "Logs", "Cookies", "WebSocket")


def _is_deleted_qobject(obj: Any) -> bool:
    """Return True when *obj* is a wrapped Qt object whose C++ instance is gone."""
    if obj is None:
        return True
    try:
        return bool(sip.isdeleted(obj))
    except Exception:
        return False


class MainWindow(
    _LayoutMixin,
    _PanelsMixin,
    _HistoryMixin,
    _ImportExportMixin,
    _EnvironmentMixin,
    _MenuMixin,
    _FramelessMixin,
    QMainWindow,
):
    """Main application window."""

    def __init__(self, db: Database) -> None:
        super().__init__()
        self.db = db
        self._history_facade = HistoryFacade(db)
        self._drag_menu_active = False
        self._drag_menu_offset = QPoint()
        self._resize_active = False
        self._drag_handles: set[Any] = set()
        self._app_event_filter_installed = False
        self._settings = get_gui_settings()
        self._intelligence_worker: IntelligenceWorker | None = None
        self._background_workers: set[Any] = set()
        self._pending_panel_refreshes: set[Any] = set()
        self.setWindowTitle("Equinox - API Testing")
        self.setGeometry(_WINDOW_X, _WINDOW_Y, _WINDOW_W, _WINDOW_H)
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.setMouseTracking(True)
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
            self._app_event_filter_installed = True
        self._layout_save_timer = QTimer(self)
        self._layout_save_timer.setSingleShot(True)
        self._layout_save_timer.setInterval(350)
        self._layout_save_timer.timeout.connect(self._save_layout)
        self._init_ui()
        log_gui_event("window_initialized", {"title": self.windowTitle()})
        self._create_menu_bar()
        self._create_status_bar()
        self._init_usage_tracking()
        self._install_navigation_shortcuts()
        self._restore_layout()
        QTimer.singleShot(0, self._maybe_run_setup_wizard)

    def _init_usage_tracking(self) -> None:
        """Attach lightweight usage tracking to menus, tabs, and tracked buttons."""
        self._ui_usage_tracker = UIUsageTracker(parent=self)
        self._ui_usage_tracker.bind_widget_tree(self)
        self._ui_usage_tracker.bind_tab_widget(self._left_tabs, context="left_tabs")
        self._ui_usage_tracker.bind_tab_widget(self.request_panel.tabs, context="request_tabs")
        menu_bar = self.menuBar()
        if menu_bar is not None:
            self._ui_usage_tracker.bind_menu_bar(menu_bar)
        self._ui_usage_tracker.record(
            "window.main_opened",
            category="lifecycle",
            context="main_window",
        )

    def _init_ui(self) -> None:
        # Import panels lazily here to avoid import-time cycles with gui.app.
        from ..request_panel import RequestPanel
        from ..response_panel import ResponsePanel

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        self._main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self._cookie_manager = CookieJarManager(self.db)
        self.collections_panel = cast(Any, None)
        self.history_panel = cast(Any, None)
        self.variables_panel = cast(Any, None)
        self.logging_panel = cast(Any, None)
        self.cookies_panel = cast(Any, None)
        self.websocket_panel = cast(Any, None)
        self._tabs_initialized: set[int] = set()
        self._left_tabs = QTabWidget()
        self._left_tabs.setObjectName("leftSidebarTabs")
        self._left_tabs.setTabPosition(QTabWidget.TabPosition.South)
        for label in _LEFT_TAB_LABELS:
            self._left_tabs.addTab(QWidget(), label)
        self._left_tabs.setMinimumWidth(_MIN_LEFT_W)
        self._left_tabs.currentChanged.connect(self._ensure_tab_initialized)
        self._left_tabs.currentChanged.connect(self._on_left_tab_changed)
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        self._req_resp_splitter = QSplitter(Qt.Orientation.Vertical)
        self.request_panel = RequestPanel(self.db, self, cookie_manager=self._cookie_manager)
        self.response_panel = ResponsePanel(self)
        self.request_panel.setMinimumHeight(_MIN_REQ_H)
        self.response_panel.setMinimumHeight(_MIN_RESP_H)
        self._req_resp_splitter.addWidget(self.request_panel)
        self._req_resp_splitter.addWidget(self.response_panel)
        self._req_resp_splitter.setSizes([_REQ_PANEL_H, _RESP_PANEL_H])
        self._req_resp_splitter.setChildrenCollapsible(False)
        self._req_resp_splitter.setHandleWidth(_SPLITTER_HANDLE_W)
        right_layout.addWidget(self._req_resp_splitter)
        self._main_splitter.addWidget(self._left_tabs)
        self._main_splitter.addWidget(right_widget)
        self._main_splitter.setSizes([_LEFT_PANEL_W, _RIGHT_PANEL_W])
        self._main_splitter.setStretchFactor(0, 0)
        self._main_splitter.setStretchFactor(1, 1)
        self._main_splitter.setChildrenCollapsible(False)
        self._main_splitter.setHandleWidth(_SPLITTER_HANDLE_W)
        main_layout.addWidget(self._main_splitter)
        self._wire_signals()
        log_gui_event("window_signals_wired", {"module": "window"})

    def _wire_signals(self) -> None:
        rp = self.request_panel
        rp.response_received.connect(self.response_panel.display_response)
        rp.response_received.connect(self._on_response_received)
        rp.response_received.connect(self._run_intelligence_analysis)
        rp.response_received.connect(
            lambda _r: self._refresh_side_panel_on_response(_TAB_COOKIES, self.cookies_panel),
        )
        rp.response_received.connect(
            lambda _r: self._refresh_side_panel_on_response(_TAB_HISTORY, self.history_panel),
        )
        self._main_splitter.splitterMoved.connect(self._on_splitter_moved)
        self._req_resp_splitter.splitterMoved.connect(self._on_splitter_moved)

    def _install_navigation_shortcuts(self) -> None:
        """Register keyboard shortcuts for the left sidebar tabs."""
        self._left_tab_shortcuts = []
        for index, label in enumerate(_LEFT_TAB_LABELS, start=1):
            shortcut = QShortcut(QKeySequence(f"Alt+{index}"), self)
            shortcut.activated.connect(lambda idx=index - 1: self._activate_left_tab(idx))
            self._left_tab_shortcuts.append(shortcut)

    def _activate_left_tab(self, index: int) -> None:
        """Focus a sidebar tab from a keyboard shortcut."""
        if not 0 <= index < self._left_tabs.count():
            return
        self._left_tabs.setCurrentIndex(index)
        self._ensure_tab_initialized(index)
        self._left_tabs.setFocus(Qt.FocusReason.ShortcutFocusReason)
        try:
            label = self._left_tabs.tabText(index)
            self.status_bar.showMessage(f"Opened {label}", 2500)
        except Exception:
            logger.exception("Failed to show left-tab navigation status", exc_info=True)

    def _on_splitter_moved(self, pos: int, index: int) -> None:
        sender = self.sender()
        if sender is not None:
            name = "main" if sender is self._main_splitter else "req/resp"
            logger.debug("%s splitter moved (pos=%d, index=%d)", name, pos, index)
        self._layout_save_timer.start()

    def closeEvent(self, event: Any | None) -> None:
        self.request_panel.autosave_current()
        if self._intelligence_worker is not None:
            worker = self._intelligence_worker
            self._intelligence_worker = None
            if not _is_deleted_qobject(worker):
                try:
                    worker.requestInterruption()
                    if not worker.wait(500):
                        worker.wait(200)
                except Exception:
                    logger.exception("Error stopping intelligence worker on close", exc_info=True)
        self._layout_save_timer.stop()
        self._save_layout()
        if self._app_event_filter_installed:
            app = QApplication.instance()
            if app is not None:
                app.removeEventFilter(self)
            self._app_event_filter_installed = False
        if hasattr(self, "_ui_usage_tracker"):
            self._ui_usage_tracker.record(
                "window.main_closed",
                category="lifecycle",
                context="main_window",
            )
            self._ui_usage_tracker.flush()
        super().closeEvent(event)

    def _load_request_guarded(self, request: Request) -> None:
        self.request_panel.autosave_current()
        self.request_panel.load_request(request)

    def _run_request_directly(self, request: Request) -> None:
        self.request_panel.autosave_current()
        self.request_panel.load_request(request)
        QTimer.singleShot(0, self.request_panel.send)

    def _new_request(self) -> None:
        self.request_panel.autosave_current()
        self.request_panel.clear()

    @staticmethod
    def _format_byte_size(size: float) -> str:
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024.0:
                return f"{size:.1f} {unit}"
            size /= 1024.0
        return f"{size:.1f} TB"

    def _build_status_message(self, response: Response) -> str:
        size_str = self._format_byte_size(float(response.size))
        status_msg = (
            f"{response.status_code} {response.reason}  -  "
            f"{int(response.elapsed * 1000)} ms  -  {size_str}"
        )
        if response.retry_summary:
            status_msg = f"{status_msg}  ({response.retry_summary})"
        return status_msg

    def _reset_intelligence_worker(self) -> None:
        if self._intelligence_worker is None:
            return

        worker = self._intelligence_worker
        self._intelligence_worker = None

        if _is_deleted_qobject(worker):
            logger.debug("Previous intelligence worker already deleted")
            return

        try:
            worker.finished.disconnect()
        except RuntimeError:
            pass
        except Exception:
            logger.exception("Could not disconnect previous intelligence worker", exc_info=True)

        try:
            worker.requestInterruption()
            if worker.isRunning():
                worker.wait(300)
        except Exception:
            logger.exception("Could not stop previous intelligence worker", exc_info=True)

    def _disabled_analyzers(self) -> set[str]:
        disabled_raw = self._settings.value(_KEY_INTEL_DISABLED, "[]")
        try:
            raw = set(json.loads(disabled_raw)) if disabled_raw else set()
            return {str(item) for item in raw}
        except Exception:
            logger.exception(
                "Invalid disabled-analyzers setting, defaulting to empty set",
                exc_info=True,
            )
            return set()

    def _on_response_received(self, response: Response) -> None:
        try:
            self.status_bar.showMessage(self._build_status_message(response), _STATUS_TIMEOUT_MS)
        except Exception:
            logger.exception("Failed to update status bar after response", exc_info=True)

    def _run_intelligence_analysis(self, response: Response) -> None:
        try:
            from equinox.gui.intelligence_worker import IntelligenceWorker

            self._reset_intelligence_worker()
            worker = IntelligenceWorker(
                request=response.request,
                response=response,
                db=self.db,
                disabled_analyzers=self._disabled_analyzers(),
                parent=self,
            )
            worker.finished.connect(self.response_panel.intelligence_panel.display_findings)
            worker.finished.connect(
                lambda findings: self.response_panel.set_intelligence_badge(len(findings)),
            )
            worker.finished.connect(worker.deleteLater)
            worker.destroyed.connect(lambda *_args: setattr(self, "_intelligence_worker", None))
            self.response_panel.intelligence_panel.set_analyzing()
            worker.start()
            self._intelligence_worker = worker
        except Exception:
            logger.warning("Intelligence analysis failed to start", exc_info=True)
