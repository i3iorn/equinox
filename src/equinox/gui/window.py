"""Main window for Equinox GUI"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, QTimer, QSettings, QByteArray
from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtWidgets import (
    QApplication,
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QTabWidget, QStatusBar, QToolButton, QMenu,
    QMessageBox, QFileDialog, QInputDialog,
)

from equinox.core.request import Request, Response
from equinox.storage import Database, EnvironmentManager, HistoryManager, CollectionManager
from equinox.storage.cookies import CookieJarManager
from equinox.gui.request_panel import RequestPanel
from equinox.gui.response_panel import ResponsePanel
from equinox.gui.theme import (
    Colors, get_font_size, set_font_size,
    DEFAULT_FONT_SIZE, get_theme_mode, set_theme_mode, THEME_MODES, THEME_LABELS,
)

logger = logging.getLogger(__name__)

_SETTINGS_KEY = "Equinox"

# ── Layout / geometry constants ───────────────────────────────────────────────
_WINDOW_X           = 100
_WINDOW_Y           = 100
_WINDOW_W           = 1400
_WINDOW_H           = 900
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


class MainWindow(QMainWindow):
    """Main application window."""

    def __init__(self, db: Database) -> None:
        super().__init__()
        self.db = db
        self._settings = QSettings(_SETTINGS_KEY, _SETTINGS_KEY)
        self._intelligence_worker = None  # keep reference to avoid GC
        self.setWindowTitle("Equinox — API Testing")
        self.setGeometry(_WINDOW_X, _WINDOW_Y, _WINDOW_W, _WINDOW_H)

        # Debounce splitter-drag saves: only flush to disk 350 ms after the
        # user *stops* dragging, instead of on every pixel movement.
        self._layout_save_timer = QTimer(self)
        self._layout_save_timer.setSingleShot(True)
        self._layout_save_timer.setInterval(350)
        self._layout_save_timer.timeout.connect(self._save_layout)

        self._init_ui()
        self._create_menu_bar()
        self._create_status_bar()
        self._restore_layout()

    # ── Layout persistence ────────────────────────────────────────────

    def _restore_layout(self) -> None:
        """Restore window geometry and splitter sizes from QSettings."""
        geo = self._settings.value(_KEY_GEOMETRY)
        if isinstance(geo, QByteArray):
            self.restoreGeometry(geo)
            logger.debug("Restored window geometry")
        state = self._settings.value(_KEY_WIN_STATE)
        if isinstance(state, QByteArray):
            self.restoreState(state)
            logger.debug("Restored window state")
        ms = self._settings.value(_KEY_MAIN_SPLIT)
        if ms is not None:
            try:
                sizes = [int(x) for x in ms]
                self._main_splitter.setSizes(sizes)
                logger.debug("Restored main splitter sizes: %s", sizes)
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
        for label in ("Collections", "History", "Variables", "Logs", "Cookies", "WebSocket"):
            self._left_tabs.addTab(QWidget(), label)
        self._left_tabs.setMinimumWidth(_MIN_LEFT_W)
        # Connect AFTER addTab so that addTab's internal currentChanged (index 0)
        # does NOT fire _ensure_tab_initialized during construction.
        self._left_tabs.currentChanged.connect(self._ensure_tab_initialized)

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

    # ── Lazy left-panel initialization ────────────────────────────────

    _LEFT_TAB_LABELS = ("Collections", "History", "Variables", "Logs", "Cookies", "WebSocket")

    # Keyboard shortcut table — displayed by _show_shortcuts_dialog.
    _KEYBOARD_SHORTCUTS: list[tuple[str, str]] = [
        ("Ctrl+N",       "New request (clear editor)"),
        ("Ctrl+Return",  "Send request"),
        ("Ctrl+S",       "Save to Collection"),
        ("Ctrl+,",       "Open Preferences"),
        ("Ctrl+Q",       "Exit"),
        ("F5",           "Refresh collections"),
        ("F1",           "Keyboard Shortcuts (this dialog)"),
        ("Ctrl+=",       "Zoom in"),
        ("Ctrl+-",       "Zoom out"),
        ("Ctrl+0",       "Reset zoom"),
        ("Ctrl+Shift+F", "Format JSON body"),
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
        logger.debug("Lazy-initialized left panel index=%d (%s)", index, label)

    def _init_collections_panel(self):
        from equinox.gui.collections_panel_pkg import CollectionsPanel
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
        rp.response_received.connect(
            lambda _r: self._safe_refresh(self.cookies_panel)
        )
        rp.response_received.connect(
            lambda _r: self._safe_refresh(self.history_panel)
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
            self.status_bar.showMessage(
                f"{code} {response.reason}  ·  {elapsed_ms} ms  ·  {size_str}",
                _STATUS_TIMEOUT_MS,
            )
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
        timestamp  = self._parse_timestamp(entry.get("executed_at"))
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

    # ── Status bar ────────────────────────────────────────────────────

    def _create_status_bar(self) -> None:
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)

        self._env_btn = QToolButton()
        self._env_btn.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self._env_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._env_btn.setToolTip("Active environment — click to switch, ▸ to manage")
        self._env_btn.setStyleSheet("border: none; padding: 0 6px;")

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
                self._env_btn.setStyleSheet(
                    f"color: {Colors.GREEN}; font-weight: bold; padding: 0 8px; border: none;"
                )
            else:
                self._env_btn.setText("No environment")
                self._env_btn.setStyleSheet(
                    f"color: {Colors.FG_SUBTLE}; padding: 0 8px; border: none;"
                )
        except Exception:
            logger.debug("Failed to refresh env label", exc_info=True)

    # ── Log file ──────────────────────────────────────────────────────

    def _open_log_file(self) -> None:
        from equinox.core.log_setup import get_log_file
        log_path = get_log_file()
        if not log_path or not log_path.exists():
            QMessageBox.information(
                self, "Log File", "No log file found yet — send a request first."
            )
            return

        # SECURITY: validate the path before handing it to OS open commands
        resolved = log_path.resolve()
        if not str(resolved).endswith(".log"):
            logger.warning("Refusing to open non-log file: %s", resolved)
            return

        try:
            if sys.platform == "win32":
                os.startfile(str(resolved))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(resolved)])  # noqa: S603
            else:
                subprocess.Popen(["xdg-open", str(resolved)])  # noqa: S603
        except Exception as exc:
            QMessageBox.information(
                self, "Log File",
                f"Log file:\n{log_path}\n\n(Could not open automatically: {exc})"
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
        """Generic import handler — opens a file dialog, runs the importer, refreshes."""
        file_path, _ = QFileDialog.getOpenFileName(self, dialog_title, "", file_filter)
        if not file_path:
            return
        mgr = CollectionManager(self.db)
        importer = importer_class(mgr)
        self.status_bar.showMessage("Importing…")
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            importer.import_file(Path(file_path))
            if self.collections_panel:
                self.collections_panel.refresh()
            self.status_bar.showMessage(success_msg, 4000)
        except Exception as exc:
            self.status_bar.clearMessage()
            QMessageBox.critical(self, "Import Error", f"Failed to import: {exc}")
        finally:
            QApplication.restoreOverrideCursor()

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
        from equinox.exporters import PostmanExporter, OpenAPIExporter, InsomniaExporter

        mgr = CollectionManager(self.db)
        collections = mgr.list_collections()
        if not collections:
            QMessageBox.warning(self, "No Collections", "No collections to export.")
            return

        col_names = [col["name"] for col in collections]
        col_name, ok = QInputDialog.getItem(
            self, "Select Collection", "Choose collection to export:", col_names, 0, False
        )
        if not ok:
            return

        # Use next() with a default to avoid StopIteration → RuntimeError (PEP 479)
        collection_id = next((c["id"] for c in collections if c["name"] == col_name), None)
        if collection_id is None:
            QMessageBox.warning(
                self, "Export Error", f"Collection '{col_name}' not found."
            )
            return

        # Offer YAML as the primary format for OpenAPI exports
        if format_type == "openapi":
            file_filter = "YAML Files (*.yaml *.yml);;JSON Files (*.json);;All Files (*)"
        else:
            file_filter = "JSON Files (*.json);;All Files (*)"

        file_path, _ = QFileDialog.getSaveFileName(
            self, f"Export as {format_type.title()}", "", file_filter
        )
        if not file_path:
            return

        try:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            if format_type == "postman":
                data = PostmanExporter.export_collection(self.db, collection_id)
                PostmanExporter.export_to_file(data, Path(file_path))
            elif format_type == "openapi":
                title, ok = QInputDialog.getText(
                    self, "OpenAPI Title", "API Title:", text=col_name
                )
                if not ok:
                    return
                data = OpenAPIExporter.export_collection(self.db, collection_id, title)
                OpenAPIExporter.export_to_file(data, Path(file_path))
            elif format_type == "insomnia":
                data = InsomniaExporter.export_collection(self.db, collection_id)
                InsomniaExporter.export_to_file(data, Path(file_path))
            self.status_bar.showMessage(f"Exported to {file_path}", 5000)
        except Exception as exc:
            QMessageBox.critical(self, "Export Error", f"Failed to export: {exc}")
        finally:
            QApplication.restoreOverrideCursor()

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
