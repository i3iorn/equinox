"""Main window for Equinox GUI"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, QTimer, QSettings, QByteArray, QEvent, QPoint
from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QTabWidget, QStatusBar, QToolButton, QMenu, QLabel,
    QMessageBox, QFileDialog, QInputDialog, QProgressDialog,
)

from equinox.gui.logging_utils import log_gui_event
from equinox.gui.log_file_actions import show_log_file_open_result, try_open_current_log_file

from equinox.core.request import Request, Response
from equinox.storage import Database, EnvironmentManager, HistoryManager, CollectionManager
from equinox.storage.cookies import CookieJarManager
from equinox.gui.request_panel import RequestPanel
from equinox.gui.response_panel import ResponsePanel
from equinox.gui.theme import (
    get_font_size, set_font_size,
    DEFAULT_FONT_SIZE, get_theme_mode, set_theme_mode, THEME_MODES, THEME_LABELS,
)

logger = logging.getLogger(__name__)

_SETTINGS_KEY = "Equinox"

# ── Layout / geometry constants ───────────────────────────────────────────────
_WINDOW_X           = 100
_WINDOW_Y           = 100
_WINDOW_W           = 1400
_WINDOW_H           = 900
# Window edge grab-zone for frameless resize.
_RESIZE_BORDER_PX   = 6
_LEFT_PANEL_W       = 300
_RIGHT_PANEL_W      = 1100
_REQ_PANEL_H        = 400
_RESP_PANEL_H       = 500
_MIN_REQ_H          = 180
_MIN_RESP_H         = 120
_MIN_LEFT_W         = 180
_SPLITTER_HANDLE_W  = 5
_STATUS_TIMEOUT_MS  = 10_000

# QSettings keys — defined once, shared between _save_layout and _restore_layout.
_KEY_GEOMETRY       = "window/geometry"
_KEY_WIN_STATE      = "window/state"
_KEY_MAIN_SPLIT     = "splitter/main"
_KEY_REQRESP_SPLIT  = "splitter/req_resp"
_KEY_LEFT_TAB       = "left_tabs/index"
_KEY_INTEL_DISABLED = "intelligence/disabled_analyzers"
_KEY_SETUP_DONE     = "onboarding/setup_wizard_completed"

_TAB_HISTORY = 1
_TAB_COOKIES = 4


class MainWindow(QMainWindow):
    """Main application window."""

    def __init__(self, db: Database) -> None:
        super().__init__()
        self.db = db
        self._drag_menu_active = False
        self._drag_menu_offset = QPoint()
        self._resize_active = False
        self._settings = QSettings(_SETTINGS_KEY, _SETTINGS_KEY)
        self._intelligence_worker = None  # keep reference to avoid GC
        self._background_workers = set()
        self._pending_panel_refreshes: set = set()
        self.setWindowTitle("Equinox — API Testing")
        self.setGeometry(_WINDOW_X, _WINDOW_Y, _WINDOW_W, _WINDOW_H)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.setMouseTracking(True)

        # Debounce splitter-drag saves: only flush to disk 350 ms after the
        # user *stops* dragging, instead of on every pixel movement.
        self._layout_save_timer = QTimer(self)
        self._layout_save_timer.setSingleShot(True)
        self._layout_save_timer.setInterval(350)
        self._layout_save_timer.timeout.connect(self._save_layout)

        self._init_ui()
        log_gui_event("window_initialized", {"title": self.windowTitle()})
        self._create_menu_bar()
        self._create_status_bar()
        self._restore_layout()
        QTimer.singleShot(0, self._maybe_run_setup_wizard)

    # ── Layout persistence ────────────────────────────────────────────

    def _restore_layout(self) -> None:
        """Restore window geometry and splitter sizes from QSettings."""
        geo = self._settings.value(_KEY_GEOMETRY)
        if isinstance(geo, QByteArray):
            self.restoreGeometry(geo)
            logger.debug("Restored window geometry")
            log_gui_event("window_geometry_restored", {"geometry_present": isinstance(geo, QByteArray)})
        state = self._settings.value(_KEY_WIN_STATE)
        if isinstance(state, QByteArray):
            self.restoreState(state)
            logger.debug("Restored window state")
            log_gui_event("window_state_restored")
        ms = self._settings.value(_KEY_MAIN_SPLIT)
        if ms is not None:
            try:
                sizes = [int(x) for x in ms]
                self._main_splitter.setSizes(sizes)
                logger.debug("Restored main splitter sizes: %s", sizes)
                log_gui_event("window_main_splitter_restored", {"sizes": sizes})
            except Exception as e:
                logger.debug("Failed to restore main splitter sizes: %s", e, exc_info=True)
        else:
            logger.debug("No saved main splitter sizes found in QSettings")
        rs = self._settings.value(_KEY_REQRESP_SPLIT)
        if rs is not None:
            try:
                sizes = [int(x) for x in rs]
                self._req_resp_splitter.setSizes(sizes)
                logger.debug("Restored req/resp splitter sizes: %s", sizes)
                log_gui_event("window_req_resp_splitter_restored", {"sizes": sizes})
            except Exception as e:
                logger.debug("Failed to restore req/resp splitter sizes: %s", e, exc_info=True)
        else:
            logger.debug("No saved req/resp splitter sizes found in QSettings")
        tab_idx = self._settings.value(_KEY_LEFT_TAB, 0, type=int)
        # Block signals so setCurrentIndex doesn't fire _ensure_tab_initialized
        # synchronously during __init__ — defer to the first event-loop iteration
        # so the window is fully constructed before any panel DB queries run.
        self._left_tabs.blockSignals(True)
        self._left_tabs.setCurrentIndex(tab_idx)
        log_gui_event("window_left_tab_restored", {"index": tab_idx, "tab": self._left_tabs.tabText(tab_idx)})
        self._left_tabs.blockSignals(False)
        QTimer.singleShot(0, lambda: self._ensure_tab_initialized(self._left_tabs.currentIndex()))
        logger.debug("Restored left tabs index: %d (initialization deferred)", tab_idx)

    def _save_layout(self) -> None:
        """Persist window geometry and splitter sizes."""
        try:
            self._settings.setValue(_KEY_GEOMETRY, self.saveGeometry())
            logger.debug("Saved window geometry")
            self._settings.setValue(_KEY_WIN_STATE, self.saveState())
            logger.debug("Saved window state")
            main_sizes = list(self._main_splitter.sizes())
            self._settings.setValue(_KEY_MAIN_SPLIT, main_sizes)
            logger.debug("Saved main splitter sizes: %s", main_sizes)
            req_resp_sizes = list(self._req_resp_splitter.sizes())
            self._settings.setValue(_KEY_REQRESP_SPLIT, req_resp_sizes)
            logger.debug("Saved req/resp splitter sizes: %s", req_resp_sizes)
            tab_idx = self._left_tabs.currentIndex()
            self._settings.setValue(_KEY_LEFT_TAB, tab_idx)
            logger.debug("Saved left tabs index: %d", tab_idx)
            self._settings.sync()  # Ensure settings are written to disk
            logger.debug("Layout settings synchronized to disk")
        except Exception as e:
            logger.error("Failed to save layout: %s", e, exc_info=True)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        logger.info("MainWindow closeEvent triggered - autosaving and persisting layout")
        self.request_panel.autosave_current()
        # Stop the intelligence worker thread gracefully before the window is
        # destroyed.  Skipping this can cause a use-after-free crash on some
        # platforms when the thread emits finished after Qt has torn down the
        # window's child objects.
        if self._intelligence_worker is not None:
            try:
                self._intelligence_worker.quit()
                if not self._intelligence_worker.wait(500):
                    logger.debug("Intelligence worker did not finish in time; terminating")
                    self._intelligence_worker.terminate()
                    self._intelligence_worker.wait(200)
            except Exception:
                logger.debug("Error stopping intelligence worker on close", exc_info=True)
            self._intelligence_worker = None
        self._layout_save_timer.stop()  # cancel any pending debounced write
        self._save_layout()
        logger.info("MainWindow closed successfully")
        super().closeEvent(event)

    # ── UI construction ───────────────────────────────────────────────

    def _init_ui(self) -> None:
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        main_layout = QHBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)

        self._main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self._cookie_manager = CookieJarManager(self.db)

        # ── Left panel (Collections, History, …) ─────────────────────
        # All six panels start as None and are created lazily on first tab click.
        self.collections_panel = None
        self.history_panel     = None
        self.variables_panel   = None
        self.logging_panel     = None
        self.cookies_panel     = None
        self.websocket_panel   = None
        self._tabs_initialized: set = set()

        self._left_tabs = QTabWidget()
        self._left_tabs.setTabPosition(QTabWidget.TabPosition.South)
        for label in ("Collections", "History", "Variables", "Logs", "Cookies", "WebSocket"):
            self._left_tabs.addTab(QWidget(), label)
        self._left_tabs.setMinimumWidth(_MIN_LEFT_W)
        # Connect AFTER addTab so that addTab's internal currentChanged (index 0)
        # does NOT fire _ensure_tab_initialized during construction.
        self._left_tabs.currentChanged.connect(self._ensure_tab_initialized)
        self._left_tabs.currentChanged.connect(self._on_left_tab_changed)

        # ── Right panel (Request / Response) ─────────────────────────
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)

        self._req_resp_splitter = QSplitter(Qt.Orientation.Vertical)
        self.request_panel  = RequestPanel(self.db, self, cookie_manager=self._cookie_manager)
        self.response_panel = ResponsePanel(self)
        # Allow both panels to shrink so the splitter handle is always draggable.
        # Without an explicit minimum the scripts-tab content inflates the
        # request panel's minimum height, leaving no room for the splitter.
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
        self._main_splitter.setStretchFactor(0, 0)   # left: fixed on resize
        self._main_splitter.setStretchFactor(1, 1)   # right: absorbs extra space
        self._main_splitter.setChildrenCollapsible(False)
        self._main_splitter.setHandleWidth(_SPLITTER_HANDLE_W)
        main_layout.addWidget(self._main_splitter)

        self._wire_signals()
        log_gui_event("window_signals_wired", {"module": "window"})

    # ── Lazy left-panel initialization ────────────────────────────────

    _LEFT_TAB_LABELS = ("Collections", "History", "Variables", "Logs", "Cookies", "WebSocket")

    # Keyboard shortcut table — displayed by _show_shortcuts_dialog.
    _KEYBOARD_SHORTCUTS: list[tuple[str, str]] = [
        ("Ctrl+N",       "New request (clear editor)"),
        ("Ctrl+L",       "Focus URL field"),
        ("Ctrl+Return",  "Send request"),
        ("Ctrl+S",       "Save to Collection"),
        ("Ctrl+,",       "Open Preferences"),
        ("Ctrl+Q",       "Exit"),
        ("F5",           "Refresh collections"),
        ("F1",           "Keyboard Shortcuts (this dialog)"),
        ("Ctrl++",       "Zoom in"),
        ("Ctrl+-",       "Zoom out"),
        ("Ctrl+0",       "Reset zoom"),
        ("Ctrl+Shift+F", "Format JSON body"),
        ("Ctrl+K",       "Command Palette"),
        ("Ctrl+F",       "Find in response body"),
        ("F2",           "Rename selected collection item"),
        ("Delete",       "Delete selected collection item"),
    ]

    def _ensure_tab_initialized(self, index: int) -> None:
        """Create the real panel for *index* on first selection; swap the placeholder."""
        if index in self._tabs_initialized:
            return
        factories = {
            0: self._init_collections_panel,
            1: self._init_history_panel,
            2: self._init_variables_panel,
            3: self._init_logging_panel,
            4: self._init_cookies_panel,
            5: self._init_websocket_panel,
        }
        factory = factories.get(index)
        if factory is None:
            return
        # Mark as initialized AFTER factory() succeeds so a failing import
        # doesn't permanently lock the tab into a broken placeholder state —
        # the user can click away and back to retry.
        try:
            panel = factory()
        except Exception:
            logger.exception("Failed to initialize left panel index=%d (%s)",
                             index, self._left_tabs.tabText(index))
            return
        self._tabs_initialized.add(index)
        # Swap placeholder for the real panel without retriggering currentChanged.
        label = self._left_tabs.tabText(index)
        self._left_tabs.blockSignals(True)
        self._left_tabs.removeTab(index)
        self._left_tabs.insertTab(index, panel, label)
        self._left_tabs.setCurrentIndex(index)
        self._left_tabs.blockSignals(False)
        self._flush_pending_panel_refresh(index)
        logger.debug("Lazy-initialized left panel index=%d (%s)", index, label)

    def _init_collections_panel(self):
        from equinox.gui.collection_panel import CollectionsPanel
        self.collections_panel = CollectionsPanel(self.db, self)
        rp = self.request_panel
        self.collections_panel.request_selected.connect(self._load_request_guarded)
        self.collections_panel.request_run.connect(self._run_request_directly)
        self.collections_panel.collections_changed.connect(
            lambda: self.collections_panel.refresh())
        self.collections_panel.collections_changed.connect(rp.refresh_inherited_auth)
        return self.collections_panel

    def _init_history_panel(self):
        from equinox.gui.history_panel import HistoryPanel
        self.history_panel = HistoryPanel(self.db, self)
        self.history_panel.history_selected.connect(self._load_history_entry)
        self.history_panel.history_replay.connect(self._replay_history_entry)
        return self.history_panel

    def _init_variables_panel(self):
        from equinox.gui.variables_panel import VariablesPanel
        self.variables_panel = VariablesPanel(self.db, self)
        rp = self.request_panel
        rp.session_vars_changed.connect(self.variables_panel.refresh_session_vars)
        self.variables_panel.clear_session_requested.connect(rp.clear_session_vars)
        return self.variables_panel

    def _init_logging_panel(self):
        from equinox.gui.logging_panel import LoggingPanel
        self.logging_panel = LoggingPanel(self)
        return self.logging_panel

    def _init_cookies_panel(self):
        from equinox.gui.cookies_panel import CookiesPanel
        self.cookies_panel = CookiesPanel(self.db, self)
        return self.cookies_panel

    def _init_websocket_panel(self):
        from equinox.gui.websocket_panel import WebSocketPanel
        self.websocket_panel = WebSocketPanel(self)
        return self.websocket_panel

    def _wire_signals(self) -> None:
        """Connect cross-panel signals for eagerly-created panels.

        Lazy-panel signals (CollectionsPanel, HistoryPanel, VariablesPanel, etc.)
        are connected in their respective _init_*_panel() methods.
        """
        rp = self.request_panel

        rp.response_received.connect(self.response_panel.display_response)
        rp.response_received.connect(self._on_response_received)
        rp.response_received.connect(self._run_intelligence_analysis)
        # Refresh lazy side-panels after each response.  Use _safe_refresh so
        # an error in one panel's refresh does not suppress the other's.
        # Hidden tabs are deferred and refreshed when selected to keep sends snappy.
        rp.response_received.connect(
            lambda _r: self._refresh_side_panel_on_response(_TAB_COOKIES, self.cookies_panel)
        )
        rp.response_received.connect(
            lambda _r: self._refresh_side_panel_on_response(_TAB_HISTORY, self.history_panel)
        )

        # Connect splitter moved signals to save layout in real-time
        self._main_splitter.splitterMoved.connect(self._on_splitter_moved)
        self._req_resp_splitter.splitterMoved.connect(self._on_splitter_moved)
        logger.debug("Connected splitter movement signals for real-time layout saving")

    def _safe_refresh(self, panel: object) -> None:
        """Call ``panel.refresh()`` if the panel exists, swallowing any error."""
        if panel is None:
            return
        try:
            panel.refresh()  # type: ignore[union-attr]
        except Exception:
            logger.debug("Panel refresh failed for %r", panel, exc_info=True)

    def _left_panel_for_index(self, index: int) -> object:
        if index == 0:
            return self.collections_panel
        if index == _TAB_HISTORY:
            return self.history_panel
        if index == 2:
            return self.variables_panel
        if index == 3:
            return self.logging_panel
        if index == _TAB_COOKIES:
            return self.cookies_panel
        if index == 5:
            return self.websocket_panel
        return None

    def _flush_pending_panel_refresh(self, index: int) -> None:
        """Apply one queued refresh for a panel when its tab becomes active."""
        if index not in self._pending_panel_refreshes:
            return
        panel = self._left_panel_for_index(index)
        if panel is None:
            return
        self._pending_panel_refreshes.discard(index)
        self._safe_refresh(panel)

    def _on_left_tab_changed(self, index: int) -> None:
        """Refresh deferred side panels when the user activates their tab."""
        self._flush_pending_panel_refresh(index)

    def _refresh_side_panel_on_response(self, index: int, panel: object) -> None:
        """Refresh panel now if visible, otherwise defer until tab activation."""
        if panel is None:
            self._pending_panel_refreshes.add(index)
            return
        if self._left_tabs.currentIndex() == index and self._left_tabs.isVisible():
            self._safe_refresh(panel)
            return
        self._pending_panel_refreshes.add(index)

    def _on_splitter_moved(self, pos: int, index: int) -> None:
        """Handle splitter movement — debounced to avoid per-pixel disk flushes."""
        sender = self.sender()
        if sender is not None:
            name = "main" if sender is self._main_splitter else "req/resp"
            logger.debug("%s splitter moved (pos=%d, index=%d)", name, pos, index)
        # Restart the debounce timer; _save_layout fires 350 ms after the last event.
        self._layout_save_timer.start()

    # ── Request / history handlers ────────────────────────────────────

    def _load_request_guarded(self, request: Request) -> None:
        """Auto-save current request then load the new one."""
        logger.debug("_load_request_guarded() called for request: %s (id=%s)", request.name, request.id)
        self.request_panel.autosave_current()
        self.request_panel.load_request(request)

    def _on_response_received(self, response: Response) -> None:
        """Update status bar with timing info after a response."""
        try:
            code       = response.status_code
            elapsed_ms = int(response.elapsed * 1000)
            size: float = response.size
            for unit in ("B", "KB", "MB", "GB"):
                if size < 1024.0:
                    size_str = f"{size:.1f} {unit}"
                    break
                size /= 1024.0
            else:
                size_str = f"{size:.1f} TB"

            # Build status message
            status_msg = f"{code} {response.reason}  ·  {elapsed_ms} ms  ·  {size_str}"

            # Add retry info if present
            if response.retry_summary:
                status_msg = f"{status_msg}  ({response.retry_summary})"

            self.status_bar.showMessage(status_msg, _STATUS_TIMEOUT_MS)
        except Exception:
            logger.debug("Failed to update status bar after response", exc_info=True)

    def _run_request_directly(self, request: Request) -> None:
        """Load a request into the editor then fire it immediately."""
        self.request_panel.autosave_current()
        self.request_panel.load_request(request)
        # Defer send() by one event-loop iteration so that all signals emitted
        # by load_request() (widget population, dirty-flag reset, etc.) are
        # fully processed before the HTTP worker reads the panel's state.
        QTimer.singleShot(0, self.request_panel.send)

    # ── Intelligence analysis ─────────────────────────────────────────

    def _run_intelligence_analysis(self, response: Response) -> None:
        """Launch a background thread to run Response Intelligence analysis."""
        try:
            from equinox.gui.intelligence_worker import IntelligenceWorker

            # Stop and discard the previous worker before creating a new one.
            # Disconnecting its finished signal prevents stale results from
            # overwriting the UI; quitting the thread avoids a background
            # analysis running to completion for a response the user no longer cares about.
            if self._intelligence_worker is not None:
                try:
                    self._intelligence_worker.finished.disconnect()
                except RuntimeError:
                    pass  # signal was already disconnected
                try:
                    self._intelligence_worker.quit()
                    self._intelligence_worker.wait(300)
                except Exception:
                    logger.debug("Could not stop previous intelligence worker", exc_info=True)
                self._intelligence_worker = None

            # Re-use the already-created QSettings instance; avoid re-parsing
            # JSON on every response.
            disabled_raw = self._settings.value(_KEY_INTEL_DISABLED, "[]")
            try:
                disabled: set = set(json.loads(disabled_raw)) if disabled_raw else set()
            except Exception:
                disabled = set()

            worker = IntelligenceWorker(
                request=response.request,
                response=response,
                db=self.db,
                disabled_analyzers=disabled,
                parent=self,
            )
            worker.finished.connect(self.response_panel.intelligence_panel.display_findings)
            worker.finished.connect(
                lambda findings: self.response_panel.set_intelligence_badge(len(findings))
            )
            # Let Qt own the C++ object once the thread finishes so we don't
            # accumulate dead QThread wrappers in memory.
            worker.finished.connect(worker.deleteLater)

            # Set "analyzing" state AFTER the worker is created so that if
            # construction raises the panel is not permanently stuck.
            self.response_panel.intelligence_panel.set_analyzing()
            worker.start()
            self._intelligence_worker = worker
        except Exception:
            logger.debug("Intelligence analysis failed to start", exc_info=True)

    # ── History handlers ──────────────────────────────────────────────

    @staticmethod
    def _coerce_to_dict(value: object, field_name: str) -> dict:
        """Return *value* as a plain dict, logging and returning ``{}`` on failure.

        Handles ``sqlite3.Row``, mapping-like objects, and any other type that
        can be coerced with ``dict()``.  Falls back to ``{}`` so callers always
        receive a safe type.
        """
        if isinstance(value, dict):
            return value
        try:
            return dict(value)  # type: ignore[arg-type]
        except Exception:
            logger.debug("Could not coerce %s to dict, defaulting to {}", field_name, exc_info=True)
            return {}

    @staticmethod
    def _coerce_body_to_bytes(raw: object) -> bytes:
        """Decode *raw* (str, bytes, or other) to ``bytes`` for Response construction."""
        if isinstance(raw, bytes):
            return raw
        if isinstance(raw, str):
            return raw.encode("utf-8")
        try:
            return str(raw).encode("utf-8")
        except Exception:
            return b""

    @staticmethod
    def _parse_timestamp(value: object) -> Optional[datetime]:
        """Parse an ISO-8601 string into a ``datetime``, or ``None`` on any failure."""
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            logger.debug("Could not parse timestamp: %s", value)
            return None

    @staticmethod
    def _request_from_history(entry: dict) -> Request:
        """Build a Request from a history DB row."""
        headers = MainWindow._coerce_to_dict(
            entry.get("request_headers") or {}, "request_headers"
        )
        params = MainWindow._coerce_to_dict(
            entry.get("request_params") or {}, "request_params"
        )

        body = entry.get("request_body")
        if isinstance(body, bytes):
            try:
                body = body.decode("utf-8")
            except Exception:
                body = body.decode("utf-8", errors="replace")
        elif body is not None and not isinstance(body, str):
            body = str(body)

        return Request(
            method=entry.get("method", "GET"),
            url=entry.get("url", ""),
            headers=headers,
            params=params,
            body=body,
        )

    def _fetch_history_entry(self, history_id: int) -> Optional[dict]:
        """Fetch a history entry by ID, or None."""
        return HistoryManager(self.db).get_history(history_id)

    def _fetch_and_load_history(self, history_id: int) -> Optional[tuple[dict, Request]]:
        """Autosave, fetch, build, and load a history entry into the request panel.

        Returns ``(entry, request)`` on success so the caller can inspect the
        DB row (e.g. to reconstruct the stored response).  Returns ``None``
        when no entry exists for *history_id*.
        """
        self.request_panel.autosave_current()
        entry = self._fetch_history_entry(history_id)
        if not entry:
            logger.debug("_fetch_and_load_history: no entry for id=%s", history_id)
            return None
        request = self._request_from_history(entry)
        try:
            self.request_panel.load_request(request)
        except Exception:
            # A UI load failure must not prevent the response from being displayed.
            logger.error("Failed to load request from history id=%s", history_id, exc_info=True)
        return entry, request

    def _build_response_from_history(
        self, entry: dict, request: Request, history_id: int
    ) -> Optional[Response]:
        """Reconstruct a ``Response`` from a history DB row, or ``None``."""
        if entry.get("status_code") is None:
            return None

        body_bytes = self._coerce_body_to_bytes(entry.get("response_body") or "")
        # Keep Response construction type-safe even when legacy rows have bad timestamps.
        timestamp  = self._parse_timestamp(entry.get("executed_at")) or datetime.now()
        headers    = self._coerce_to_dict(
            entry.get("response_headers") or {}, "response_headers"
        )

        try:
            response = Response(
                status_code=int(entry.get("status_code") or 0),
                reason=entry.get("reason") or "",
                headers=headers,
                body=body_bytes,
                elapsed=float(entry.get("elapsed") or 0.0),
                request=request,
                timestamp=timestamp,
            )
        except Exception:
            logger.error(
                "Failed to construct Response for history id=%s", history_id, exc_info=True
            )
            return None

        logger.debug(
            "Built history response id=%s (status=%s size=%s)",
            history_id, entry.get("status_code"), len(body_bytes),
        )
        return response

    def _load_history_entry(self, history_id: int) -> None:
        """Load and display a history entry in the request/response panels."""
        try:
            result = self._fetch_and_load_history(history_id)
            if result is None:
                return
            entry, request = result
            response = self._build_response_from_history(entry, request, history_id)
            if response is not None:
                self.response_panel.display_response(response)
        except Exception:
            # Catch-all: the UI must stay responsive even on corrupted DB rows.
            logger.error(
                "Unhandled error loading history entry id=%s", history_id, exc_info=True
            )
            try:
                QMessageBox.critical(
                    self, "Error",
                    f"Failed to load history entry {history_id}. See log for details.",
                )
            except Exception:
                logger.debug("Also failed to show error dialog for history load", exc_info=True)

    def _replay_history_entry(self, history_id: int) -> None:
        """Re-run a history entry exactly as originally sent."""
        if self._fetch_and_load_history(history_id) is None:
            return
        # Defer send() so load_request()'s widget-population signals are fully
        # processed before the HTTP worker reads the panel state.
        QTimer.singleShot(0, self.request_panel.send)

    def _new_request(self) -> None:
        """Autosave current request then clear the editor for a new one."""
        self.request_panel.autosave_current()
        self.request_panel.clear()

    # ── Menu bar ──────────────────────────────────────────────────────

    def _create_menu_bar(self) -> None:
        menubar = self.menuBar()
        if menubar is None:
            return

        # File
        file_menu = menubar.addMenu("&File")
        new_req = QAction("&New Request", self)
        new_req.setShortcut("Ctrl+N")
        new_req.triggered.connect(self._new_request)
        file_menu.addAction(new_req)
        file_menu.addSeparator()

        import_menu = file_menu.addMenu("&Import")
        for label, slot in [
            ("Postman Collection…",    self._import_postman),
            ("OpenAPI/Swagger Spec…",  self._import_openapi),
            ("HAR File…",              self._import_har),
            ("Insomnia Collection…",   self._import_insomnia),
        ]:
            act = QAction(label, self)
            act.triggered.connect(slot)
            import_menu.addAction(act)

        file_menu.addSeparator()
        exit_act = QAction("E&xit", self)
        exit_act.setShortcut("Ctrl+Q")
        exit_act.triggered.connect(self.close)
        file_menu.addAction(exit_act)

        # View
        view_menu = menubar.addMenu("&View")
        for label, shortcut, slot in [
            ("Zoom &In",    "Ctrl+=", self._zoom_in),
            ("Zoom &Out",   "Ctrl+-", self._zoom_out),
            ("&Reset Zoom", "Ctrl+0", self._zoom_reset),
        ]:
            act = QAction(label, self)
            act.setShortcut(QKeySequence(shortcut))
            act.triggered.connect(slot)
            view_menu.addAction(act)
        view_menu.addSeparator()

        theme_menu = view_menu.addMenu("&Theme")
        self._theme_actions: dict = {}
        for mode in THEME_MODES:
            a = QAction(THEME_LABELS[mode], self)
            a.setCheckable(True)
            a.setChecked(mode == get_theme_mode())
            a.triggered.connect(lambda checked, m=mode: self._set_theme(m))
            theme_menu.addAction(a)
            self._theme_actions[mode] = a

        view_menu.addSeparator()
        cmd_palette = QAction("Command &Palette…", self)
        cmd_palette.setShortcut(QKeySequence("Ctrl+K"))
        cmd_palette.triggered.connect(self._open_command_palette)
        view_menu.addAction(cmd_palette)

        view_menu.addSeparator()
        prefs_act = QAction("&Preferences…", self)
        prefs_act.setShortcut("Ctrl+,")
        prefs_act.triggered.connect(self._open_preferences)
        view_menu.addAction(prefs_act)

        # Collections
        col_menu = menubar.addMenu("&Collections")
        new_col = QAction("New &Collection", self)
        new_col.triggered.connect(
            lambda: self.collections_panel.create_collection() if self.collections_panel else None
        )
        col_menu.addAction(new_col)
        refresh_act = QAction("&Refresh", self)
        refresh_act.setShortcut("F5")
        refresh_act.triggered.connect(
            lambda: self.collections_panel.refresh() if self.collections_panel else None
        )
        col_menu.addAction(refresh_act)
        col_menu.addSeparator()
        export_menu = col_menu.addMenu("&Export")
        for label, fmt in [
            ("Postman Format…",  "postman"),
            ("OpenAPI Format…",  "openapi"),
            ("Insomnia Format…", "insomnia"),
        ]:
            a = QAction(label, self)
            a.triggered.connect(lambda checked, f=fmt: self._export_collection(f))
            export_menu.addAction(a)

        # Environment
        env_menu = menubar.addMenu("E&nvironment")
        manage_env = QAction("&Manage Environments…", self)
        manage_env.triggered.connect(self._manage_environments)
        env_menu.addAction(manage_env)
        manage_creds = QAction("Manage &Saved Credentials…", self)
        manage_creds.triggered.connect(self._manage_oauth_clients)
        env_menu.addAction(manage_creds)
        manage_secrets = QAction("Manage &Secret Managers…", self)
        manage_secrets.triggered.connect(self._manage_secret_managers)
        env_menu.addAction(manage_secrets)

        # Help
        help_menu = menubar.addMenu("&Help")
        shortcuts_act = QAction("&Keyboard Shortcuts…", self)
        shortcuts_act.setShortcut(QKeySequence("F1"))
        shortcuts_act.triggered.connect(self._show_shortcuts_dialog)
        help_menu.addAction(shortcuts_act)
        help_menu.addSeparator()
        log_act = QAction("&View Log File…", self)
        log_act.triggered.connect(self._open_log_file)
        help_menu.addAction(log_act)
        help_menu.addSeparator()
        about_act = QAction("&About", self)
        about_act.triggered.connect(self._show_about)
        help_menu.addAction(about_act)
        help_menu.addSeparator()
        setup_act = QAction("Run Setup Wizard…", self)
        setup_act.triggered.connect(self._run_setup_wizard)
        help_menu.addAction(setup_act)

        self._add_window_controls_to_menu_bar(menubar)
        menubar.installEventFilter(self)

    def setWindowTitle(self, title: str) -> None:  # type: ignore[override]
        """Keep the menu-bar title label synchronized with the window title."""
        super().setWindowTitle(title)
        if hasattr(self, "_menu_title_label"):
            self._menu_title_label.setText(title)

    def _add_window_controls_to_menu_bar(self, menubar) -> None:
        """Attach frameless-window controls to the right side of the menu bar."""
        title_container = QWidget(menubar)
        title_container.setObjectName("menuBarWindowTitleContainer")
        title_layout = QHBoxLayout(title_container)
        title_layout.setContentsMargins(18, 0, 18, 0)

        self._menu_title_label = QLabel(self.windowTitle(), title_container)
        self._menu_title_label.setObjectName("menuBarWindowTitle")
        self._menu_title_label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self._menu_title_label.setCursor(Qt.CursorShape.ArrowCursor)
        title_layout.addWidget(self._menu_title_label)

        menubar.setCornerWidget(title_container, Qt.Corner.TopLeftCorner)
        title_container.installEventFilter(self)
        self._menu_title_label.installEventFilter(self)

        container = QWidget(menubar)
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._win_min_btn = QToolButton(container)
        self._win_min_btn.setText("—")
        self._win_min_btn.setToolTip("Minimize")
        self._win_min_btn.clicked.connect(self.showMinimized)

        self._win_max_btn = QToolButton(container)
        self._win_max_btn.clicked.connect(self._toggle_max_restore)

        self._win_close_btn = QToolButton(container)
        self._win_close_btn.setText("✕")
        self._win_close_btn.setToolTip("Close")
        self._win_close_btn.clicked.connect(self.close)

        for btn in (self._win_min_btn, self._win_max_btn, self._win_close_btn):
            btn.setObjectName("windowControlBtn")
            btn.setFixedSize(28, 22)
            layout.addWidget(btn)

        menubar.setCornerWidget(container, Qt.Corner.TopRightCorner)
        self._sync_window_controls()

    def eventFilter(self, watched, event):  # type: ignore[override]
        """Enable dragging the frameless window from empty menu-bar/title area."""
        if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            menu_bar = self.menuBar()
            if watched is menu_bar:
                action = menu_bar.actionAt(event.pos())
                if action is not None:
                    self._drag_menu_active = False
                    return super().eventFilter(watched, event)
            self._drag_menu_active = not self.isMaximized() and not self.isFullScreen()
            if self._drag_menu_active:
                self._drag_menu_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            return False

        if event.type() == QEvent.Type.MouseMove and self._drag_menu_active:
            if event.buttons() != Qt.MouseButton.NoButton:
                self.move(event.globalPosition().toPoint() - self._drag_menu_offset)
                return True
            self._drag_menu_active = False

        if event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
            self._drag_menu_active = False

        return super().eventFilter(watched, event)

    def _toggle_max_restore(self) -> None:
        """Toggle between maximized and normal states."""
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()
        self._sync_window_controls()

    def _sync_window_controls(self) -> None:
        """Update maximize button icon/tooltip to reflect current window state."""
        if not hasattr(self, "_win_max_btn"):
            return
        if self.isMaximized():
            self._win_max_btn.setText("❐")
            self._win_max_btn.setToolTip("Restore")
        else:
            self._win_max_btn.setText("□")
            self._win_max_btn.setToolTip("Maximize")

    def changeEvent(self, event) -> None:  # type: ignore[override]
        if event.type() == QEvent.Type.WindowStateChange:
            self._sync_window_controls()
            if not self._can_resize_frameless():
                self.setCursor(Qt.CursorShape.ArrowCursor)
        super().changeEvent(event)

    # ── Zoom ──────────────────────────────────────────────────────────

    def _adjust_font_size(self, delta: int) -> None:
        """Clamp and apply a font-size change of *delta* points."""
        set_font_size(get_font_size() + delta)

    def _zoom_in(self) -> None:
        self._adjust_font_size(+1)

    def _zoom_out(self) -> None:
        self._adjust_font_size(-1)

    def _zoom_reset(self) -> None:
        set_font_size(DEFAULT_FONT_SIZE)

    def _set_theme(self, mode: str) -> None:
        set_theme_mode(mode)
        self._sync_theme_checks()

    def _sync_theme_checks(self) -> None:
        """Keep the theme radio-check marks in sync with the current mode."""
        current = get_theme_mode()
        for mode, action in self._theme_actions.items():
            action.setChecked(mode == current)

    def _open_preferences(self) -> None:
        from equinox.gui.dialogs.preferences_dialog import PreferencesDialog
        PreferencesDialog(self).exec()
        self._sync_theme_checks()

    def _maybe_run_setup_wizard(self) -> None:
        """Run onboarding wizard once per profile on first launch."""
        if os.environ.get("PYTEST_CURRENT_TEST"):
            return
        if self._settings.value(_KEY_SETUP_DONE, False, type=bool):
            return
        self._run_setup_wizard(mark_complete_on_cancel=False)

    def _run_setup_wizard(self, mark_complete_on_cancel: bool = True) -> None:
        """Open setup wizard and apply selected onboarding choices."""
        from equinox.gui.dialogs.setup_wizard_dialog import SetupWizardDialog

        dlg = SetupWizardDialog(self)
        if dlg.exec() != dlg.DialogCode.Accepted:
            if mark_complete_on_cancel:
                self._settings.setValue(_KEY_SETUP_DONE, True)
            return

        data = dlg.result_data()
        selected_theme = data.get("theme_mode")
        if isinstance(selected_theme, str):
            self._set_theme(selected_theme)

        self._settings.setValue(_KEY_SETUP_DONE, True)
        self.status_bar.showMessage("Setup complete", 3000)

        if data.get("open_environment_manager"):
            QTimer.singleShot(0, self._manage_environments)
        if data.get("open_saved_credentials"):
            QTimer.singleShot(0, self._manage_oauth_clients)

    def _command_palette_items(self) -> list:
        """Return command palette entries with stable IDs and callbacks."""
        return [
            {"id": "new_request", "label": "New Request", "shortcut": "Ctrl+N", "callback": self._new_request},
            {"id": "send_request", "label": "Send Request", "shortcut": "Ctrl+Enter", "callback": self.request_panel.send},
            {"id": "save_request", "label": "Save Request", "shortcut": "Ctrl+S", "callback": self.request_panel._save_request},
            {"id": "focus_url", "label": "Focus URL", "shortcut": "Ctrl+L", "callback": self.request_panel._focus_url_input},
            {"id": "import_postman", "label": "Import Postman", "callback": self._import_postman},
            {"id": "import_openapi", "label": "Import OpenAPI", "callback": self._import_openapi},
            {"id": "import_har", "label": "Import HAR", "callback": self._import_har},
            {"id": "import_insomnia", "label": "Import Insomnia", "callback": self._import_insomnia},
            {"id": "export_postman", "label": "Export Collection as Postman", "callback": lambda: self._export_collection("postman")},
            {"id": "export_openapi", "label": "Export Collection as OpenAPI", "callback": lambda: self._export_collection("openapi")},
            {"id": "export_insomnia", "label": "Export Collection as Insomnia", "callback": lambda: self._export_collection("insomnia")},
            {"id": "manage_env", "label": "Manage Environments", "callback": self._manage_environments},
            {"id": "preferences", "label": "Open Preferences", "shortcut": "Ctrl+,", "callback": self._open_preferences},
            {"id": "setup_wizard", "label": "Run Setup Wizard", "callback": self._run_setup_wizard},
        ]

    def _open_command_palette(self) -> None:
        """Open searchable command palette and execute selected command."""
        from equinox.gui.dialogs.command_palette_dialog import CommandPaletteDialog

        commands = self._command_palette_items()
        view_items = [{"id": c["id"], "label": c["label"], "shortcut": c.get("shortcut", "")} for c in commands]
        dlg = CommandPaletteDialog(view_items, self)
        if dlg.exec() != dlg.DialogCode.Accepted:
            return

        selected_id = dlg.selected_command_id()
        if not selected_id:
            return
        command = next((c for c in commands if c["id"] == selected_id), None)
        if command is None:
            return

        callback = command.get("callback")
        try:
            if callable(callback):
                callback()
        except Exception:
            logger.error("Command palette command failed: %s", selected_id, exc_info=True)
            QMessageBox.warning(self, "Command Failed", f"Could not execute command: {selected_id}")

    # ── Status bar ────────────────────────────────────────────────────

    def _create_status_bar(self) -> None:
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)

        self._env_btn = QToolButton()
        self._env_btn.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self._env_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._env_btn.setToolTip("Active environment — click to switch, ▸ to manage")

        self._env_menu = QMenu(self._env_btn)
        self._env_btn.setMenu(self._env_menu)
        self._env_btn.clicked.connect(self._show_env_menu)
        self.status_bar.addPermanentWidget(self._env_btn)

        self._refresh_env_label()
        self.status_bar.showMessage("Ready")

    def _show_env_menu(self) -> None:
        """Build and show the environment quick-switch menu."""
        self._env_menu.clear()
        try:
            mgr = EnvironmentManager(self.db)
            envs = mgr.list_environments()
            active = mgr.get_active_environment()
            active_id = active["id"] if active else None

            for env in envs:
                action = self._env_menu.addAction(env["name"])
                action.setCheckable(True)
                action.setChecked(env["id"] == active_id)
                action.triggered.connect(
                    lambda checked, eid=env["id"]: self._switch_environment(eid)
                )
            if envs:
                self._env_menu.addSeparator()

            manage = self._env_menu.addAction("Manage Environments…")
            manage.triggered.connect(self._manage_environments)
        except Exception:
            logger.debug("Failed to build env menu", exc_info=True)
            manage = self._env_menu.addAction("Manage Environments…")
            manage.triggered.connect(self._manage_environments)

        self._env_menu.popup(
            self._env_btn.mapToGlobal(self._env_btn.rect().topLeft())
        )

    def _switch_environment(self, env_id: int) -> None:
        """Activate the given environment and refresh the label."""
        try:
            EnvironmentManager(self.db).set_active_environment(env_id)
            self._refresh_env_label()
            self.request_panel.refresh_inherited_auth()
        except Exception:
            logger.debug("Failed to switch environment to %d", env_id, exc_info=True)

    def _refresh_env_label(self) -> None:
        try:
            env = EnvironmentManager(self.db).get_active_environment()
            if env:
                self._env_btn.setText(f"🌍  {env['name']}")
            else:
                self._env_btn.setText("No environment")
        except Exception:
            logger.debug("Failed to refresh env label", exc_info=True)

    # ── Log file ──────────────────────────────────────────────────────

    def _open_log_file(self) -> None:
        log_gui_event("log_file_open_requested")
        show_log_file_open_result(
            self,
            try_open_current_log_file(),
            "No log file found yet — send a request first.",
        )

    # ── Shortcuts dialog ──────────────────────────────────────────────

    def _show_shortcuts_dialog(self) -> None:
        """Display a read-only table of all keyboard shortcuts."""
        from PyQt6.QtWidgets import (
            QDialog, QVBoxLayout, QTableWidget, QTableWidgetItem,
            QHeaderView, QDialogButtonBox,
        )

        dialog = QDialog(self)
        dialog.setWindowTitle("Keyboard Shortcuts")
        dialog.setMinimumSize(480, 400)
        dlg_layout = QVBoxLayout(dialog)

        table = QTableWidget(len(self._KEYBOARD_SHORTCUTS), 2)
        table.setHorizontalHeaderLabels(["Shortcut", "Action"])
        table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setAlternatingRowColors(True)
        for row, (shortcut, action) in enumerate(self._KEYBOARD_SHORTCUTS):
            table.setItem(row, 0, QTableWidgetItem(shortcut))
            table.setItem(row, 1, QTableWidgetItem(action))
        table.resizeRowsToContents()

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        dlg_layout.addWidget(table, 1)
        dlg_layout.addWidget(buttons)
        dialog.exec()

    def _show_about(self) -> None:
        from equinox import __version__
        QMessageBox.about(
            self, "About Equinox",
            f"<h2>Equinox v{__version__}</h2>"
            "<p>A local-first API testing tool</p>"
            "<p>Built with Python and PyQt6</p>",
        )

    # ── Import / Export ───────────────────────────────────────────────

    def _import_with(
        self,
        importer_class,
        dialog_title: str,
        file_filter: str,
        success_msg: str,
    ) -> None:
        """Generic import handler with background execution and retry."""
        file_path, _ = QFileDialog.getOpenFileName(self, dialog_title, "", file_filter)
        if not file_path:
            return
        self._start_import(importer_class, Path(file_path), success_msg)

    def _start_import(self, importer_class, file_path: Path, success_msg: str) -> None:
        """Run selected importer in background with retry on error."""

        def _operation() -> bool:
            mgr = CollectionManager(self.db)
            importer = importer_class(mgr)
            importer.import_file(file_path)
            return True

        self._run_background_task(
            operation=_operation,
            operation_name=f"Importing {file_path.name}...",
            success_msg=success_msg,
            error_title="Import Error",
            on_success=lambda _result: self._refresh_collections_after_background(),
            retry_operation=lambda: self._start_import(importer_class, file_path, success_msg),
        )

    def _refresh_collections_after_background(self) -> None:
        """Refresh collections panel now, or queue refresh until tab is opened."""
        if self.collections_panel is not None:
            self._safe_refresh(self.collections_panel)
            return
        self._pending_panel_refreshes.add(0)

    def _run_background_task(
        self,
        operation,
        operation_name: str,
        success_msg: str,
        error_title: str,
        on_success=None,
        retry_operation=None,
    ) -> None:
        """Execute a blocking operation on a worker thread with progress UX."""
        from equinox.gui.workers import BackgroundTaskWorker

        progress = QProgressDialog(operation_name, "Cancel", 0, 0, self)
        progress.setWindowTitle("Working")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.show()

        worker = BackgroundTaskWorker(operation, parent=self)
        self._background_workers.add(worker)

        def _cleanup() -> None:
            try:
                progress.close()
            except Exception:
                pass
            self._background_workers.discard(worker)
            worker.deleteLater()

        def _on_cancel() -> None:
            worker.cancel()
            progress.setLabelText("Cancelling...")
            progress.setCancelButton(None)

        def _on_finished(success: bool, payload: object) -> None:
            _cleanup()
            if success:
                self.status_bar.showMessage(success_msg, 4000)
                if callable(on_success):
                    on_success(payload)
                return

            error_text = str(payload)
            retry_btn = QMessageBox.StandardButton.Retry
            cancel_btn = QMessageBox.StandardButton.Cancel
            choice = QMessageBox.question(
                self,
                error_title,
                f"{error_text}\n\nRetry the operation?",
                retry_btn | cancel_btn,
                retry_btn,
            )
            if choice == retry_btn and callable(retry_operation):
                retry_operation()

        progress.canceled.connect(_on_cancel)
        worker.finished.connect(_on_finished)
        worker.start()

    def _import_postman(self) -> None:
        from equinox.importers import PostmanImporter
        self._import_with(
            PostmanImporter,
            "Import Postman Collection",
            "JSON Files (*.json);;All Files (*)",
            "Postman collection imported",
        )

    def _import_openapi(self) -> None:
        from equinox.importers import OpenAPIImporter
        self._import_with(
            OpenAPIImporter,
            "Import OpenAPI/Swagger Specification",
            "API Spec Files (*.json *.yaml *.yml);;All Files (*)",
            "OpenAPI specification imported",
        )

    def _import_har(self) -> None:
        from equinox.importers import HARImporter
        self._import_with(
            HARImporter,
            "Import HAR File",
            "HAR Files (*.har);;JSON Files (*.json);;All Files (*)",
            "HAR file imported",
        )

    def _import_insomnia(self) -> None:
        from equinox.importers import InsomniaImporter
        self._import_with(
            InsomniaImporter,
            "Import Insomnia Collection",
            "JSON Files (*.json);;All Files (*)",
            "Insomnia collection imported",
        )

    def _export_collection(self, format_type: str) -> None:
        mgr = CollectionManager(self.db)
        collections = mgr.list_collections()
        if not collections:
            QMessageBox.warning(self, "No Collections", "No collections to export.")
            return

        col_names = [col["name"] for col in collections]
        col_name, ok = QInputDialog.getItem(
            self, "Select Collection", "Choose collection to export:", col_names, 0, False
        )
        if not ok or not isinstance(col_name, str) or not col_name:
            return

        # Use next() with a default to avoid StopIteration → RuntimeError (PEP 479)
        collection_id = next((c["id"] for c in collections if c["name"] == col_name), None)
        if collection_id is None:
            QMessageBox.warning(
                self, "Export Error", f"Collection '{col_name}' not found."
            )
            return
        collection_id = int(collection_id)

        # Offer YAML as the primary format for OpenAPI exports
        if format_type == "openapi":
            file_filter = "YAML Files (*.yaml *.yml);;JSON Files (*.json);;All Files (*)"
        else:
            file_filter = "JSON Files (*.json);;All Files (*)"

        file_path, _ = QFileDialog.getSaveFileName(
            self, f"Export as {format_type.title()}", "", file_filter
        )
        if not isinstance(file_path, str) or not file_path:
            return

        openapi_title = col_name
        if format_type == "openapi":
            title, ok = QInputDialog.getText(
                self, "OpenAPI Title", "API Title:", text=col_name
            )
            if not ok:
                return
            openapi_title = title

        self._start_export(
            format_type=format_type,
            collection_id=collection_id,
            file_path=Path(file_path),
            openapi_title=openapi_title,
        )

    def _start_export(
        self,
        format_type: str,
        collection_id: int,
        file_path: Path,
        openapi_title: str,
    ) -> None:
        """Run collection export in the background with retry support."""
        from equinox.exporters import PostmanExporter, OpenAPIExporter, InsomniaExporter

        def _operation() -> str:
            if format_type == "postman":
                data = PostmanExporter.export_collection(self.db, collection_id)
                PostmanExporter.export_to_file(data, file_path)
            elif format_type == "openapi":
                data = OpenAPIExporter.export_collection(self.db, collection_id, openapi_title)
                OpenAPIExporter.export_to_file(data, file_path)
            elif format_type == "insomnia":
                data = InsomniaExporter.export_collection(self.db, collection_id)
                InsomniaExporter.export_to_file(data, file_path)
            else:
                raise ValueError(f"Unsupported export format: {format_type}")
            return str(file_path)

        self._run_background_task(
            operation=_operation,
            operation_name=f"Exporting {file_path.name}...",
            success_msg=f"Exported to {file_path}",
            error_title="Export Error",
            retry_operation=lambda: self._start_export(
                format_type=format_type,
                collection_id=collection_id,
                file_path=file_path,
                openapi_title=openapi_title,
            ),
        )

    # ── Environment management ────────────────────────────────────────

    def _manage_environments(self) -> None:
        from equinox.gui.dialogs.environment_dialog import EnvironmentDialog
        EnvironmentDialog(self.db, self).exec()
        # Single post-exec refresh covers both changed and cancelled/dismissed.
        if self.variables_panel:
            self.variables_panel.refresh()
        self._refresh_env_label()

    def _manage_oauth_clients(self) -> None:
        from equinox.gui.dialogs.saved_credentials_dialog import SavedCredentialsDialog
        SavedCredentialsDialog(self.db, self).exec()

    def _manage_secret_managers(self) -> None:
        """Open the dedicated Secret Manager settings dialog."""
        from equinox.gui.dialogs.secret_manager_settings_dialog import SecretManagerSettingsDialog

        SecretManagerSettingsDialog(parent=self).exec()

    def _resize_edges_for_pos(self, pos: QPoint) -> Qt.Edge:
        """Return the window-edge flags under *pos* for frameless resizing."""
        x = pos.x()
        y = pos.y()
        w = self.width()
        h = self.height()

        edges = Qt.Edge(0)
        if x <= _RESIZE_BORDER_PX:
            edges |= Qt.Edge.LeftEdge
        elif x >= w - _RESIZE_BORDER_PX:
            edges |= Qt.Edge.RightEdge

        if y <= _RESIZE_BORDER_PX:
            edges |= Qt.Edge.TopEdge
        elif y >= h - _RESIZE_BORDER_PX:
            edges |= Qt.Edge.BottomEdge

        return edges

    def _can_resize_frameless(self) -> bool:
        """Frameless resize is enabled only in normal windowed mode."""
        return not self.isMaximized() and not self.isFullScreen()

    def _cursor_for_edges(self, edges: Qt.Edge) -> Qt.CursorShape:
        """Map edge flags to the expected resize cursor shape."""
        has_left = bool(edges & Qt.Edge.LeftEdge)
        has_right = bool(edges & Qt.Edge.RightEdge)
        has_top = bool(edges & Qt.Edge.TopEdge)
        has_bottom = bool(edges & Qt.Edge.BottomEdge)

        if (has_left and has_top) or (has_right and has_bottom):
            return Qt.CursorShape.SizeFDiagCursor
        if (has_right and has_top) or (has_left and has_bottom):
            return Qt.CursorShape.SizeBDiagCursor
        if has_left or has_right:
            return Qt.CursorShape.SizeHorCursor
        if has_top or has_bottom:
            return Qt.CursorShape.SizeVerCursor
        return Qt.CursorShape.ArrowCursor

    def _update_resize_cursor(self, pos: QPoint) -> None:
        """Show resize cursors near window borders when resize is available."""
        if not self._can_resize_frameless() or self._drag_menu_active:
            self.setCursor(Qt.CursorShape.ArrowCursor)
            return
        edges = self._resize_edges_for_pos(pos)
        self.setCursor(self._cursor_for_edges(edges))

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self._can_resize_frameless()
            and not self._drag_menu_active
        ):
            edges = self._resize_edges_for_pos(event.position().toPoint())
            if edges:
                handle = self.windowHandle()
                if handle is not None and handle.startSystemResize(edges):
                    self._resize_active = True
                    event.accept()
                    return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        self._update_resize_cursor(event.position().toPoint())
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        self._resize_active = False
        self._update_resize_cursor(event.position().toPoint())
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event) -> None:  # type: ignore[override]
        if not self._resize_active:
            self.setCursor(Qt.CursorShape.ArrowCursor)
        super().leaveEvent(event)
