"""Main window for Equinox GUI"""

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
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QTabWidget, QStatusBar, QToolButton, QMenu,
    QMessageBox, QFileDialog, QInputDialog,
)

from equinox.core.request import Request, Response
from equinox.storage import Database, EnvironmentManager, HistoryManager, CollectionManager
from equinox.storage.cookies import CookieJarManager
from equinox.gui.request_panel import RequestPanel
from equinox.gui.response_panel import ResponsePanel
from equinox.gui.collections_panel_pkg import CollectionsPanel
from equinox.gui.history_panel import HistoryPanel
from equinox.gui.variables_panel import VariablesPanel
from equinox.gui.logging_panel import LoggingPanel
from equinox.gui.cookies_panel import CookiesPanel
from equinox.gui.websocket_panel import WebSocketPanel
from equinox.gui.theme import (
    Colors, get_font_size, set_font_size, MIN_FONT_SIZE, MAX_FONT_SIZE,
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


class MainWindow(QMainWindow):
    """Main application window."""

    def __init__(self, db: Database) -> None:
        super().__init__()
        self.db = db
        self._settings = QSettings(_SETTINGS_KEY, _SETTINGS_KEY)
        self._intelligence_worker = None  # keep reference to avoid GC
        self.setWindowTitle("Equinox — API Testing")
        self.setGeometry(_WINDOW_X, _WINDOW_Y, _WINDOW_W, _WINDOW_H)

        self._init_ui()
        self._create_menu_bar()
        self._create_status_bar()
        self._restore_layout()

    # ── Layout persistence ────────────────────────────────────────────

    def _restore_layout(self) -> None:
        """Restore window geometry and splitter sizes from QSettings."""
        geo = self._settings.value("window/geometry")
        if isinstance(geo, QByteArray):
            self.restoreGeometry(geo)
        state = self._settings.value("window/state")
        if isinstance(state, QByteArray):
            self.restoreState(state)
        ms = self._settings.value("splitter/main")
        if ms is not None:
            try:
                self._main_splitter.setSizes([int(x) for x in ms])
            except Exception:
                logger.debug("Failed to restore main splitter sizes", exc_info=True)
        rs = self._settings.value("splitter/req_resp")
        if rs is not None:
            try:
                self._req_resp_splitter.setSizes([int(x) for x in rs])
            except Exception:
                logger.debug("Failed to restore req/resp splitter sizes", exc_info=True)
        tab_idx = self._settings.value("left_tabs/index", 0, type=int)
        self._left_tabs.setCurrentIndex(tab_idx)

    def _save_layout(self) -> None:
        """Persist window geometry and splitter sizes."""
        self._settings.setValue("window/geometry", self.saveGeometry())
        self._settings.setValue("window/state", self.saveState())
        self._settings.setValue("splitter/main", self._main_splitter.sizes())
        self._settings.setValue("splitter/req_resp", self._req_resp_splitter.sizes())
        self._settings.setValue("left_tabs/index", self._left_tabs.currentIndex())

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self.request_panel.autosave_current()
        self._save_layout()
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
        self._left_tabs = QTabWidget()
        self.collections_panel = CollectionsPanel(self.db, self)
        self.history_panel     = HistoryPanel(self.db, self)
        self.variables_panel   = VariablesPanel(self.db, self)
        self.logging_panel     = LoggingPanel(self)
        self.cookies_panel     = CookiesPanel(self.db, self)
        self.websocket_panel   = WebSocketPanel(self)
        self._left_tabs.addTab(self.collections_panel, "Collections")
        self._left_tabs.addTab(self.history_panel,     "History")
        self._left_tabs.addTab(self.variables_panel,   "Variables")
        self._left_tabs.addTab(self.logging_panel,     "Logs")
        self._left_tabs.addTab(self.cookies_panel,     "Cookies")
        self._left_tabs.addTab(self.websocket_panel,   "WebSocket")
        self._left_tabs.setMinimumWidth(_MIN_LEFT_W)

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

    def _wire_signals(self) -> None:
        """Connect all cross-panel signals in one place."""
        rp = self.request_panel

        rp.response_received.connect(self.response_panel.display_response)
        rp.response_received.connect(self._on_response_received)
        rp.response_received.connect(self._run_intelligence_analysis)
        # Refresh side-panels after each response
        rp.response_received.connect(lambda _r: self.cookies_panel.refresh())
        rp.response_received.connect(lambda _r: self.history_panel.refresh())

        self.collections_panel.request_selected.connect(self._load_request_guarded)
        self.collections_panel.request_run.connect(self._run_request_directly)
        self.collections_panel.collections_changed.connect(
            lambda: self.collections_panel.refresh())
        self.collections_panel.collections_changed.connect(
            rp.refresh_inherited_auth)

        self.history_panel.history_selected.connect(self._load_history_entry)
        self.history_panel.history_replay.connect(self._replay_history_entry)

        rp.session_vars_changed.connect(self.variables_panel.refresh_session_vars)
        self.variables_panel.clear_session_requested.connect(rp.clear_session_vars)

    # ── Request / history handlers ────────────────────────────────────

    def _load_request_guarded(self, request: Request) -> None:
        """Auto-save current request then load the new one."""
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
        # TODO: replace with request_panel.send() once a public method is added
        self.request_panel._send_request()  # noqa: SLF001

    # ── Intelligence analysis ─────────────────────────────────────────

    def _run_intelligence_analysis(self, response: Response) -> None:
        """Launch a background thread to run Response Intelligence analysis."""
        try:
            from equinox.gui.intelligence_worker import IntelligenceWorker

            # Disconnect the previous worker's finished signal before replacing
            # it so stale results cannot overwrite the new ones.
            if self._intelligence_worker is not None:
                try:
                    self._intelligence_worker.finished.disconnect()
                except RuntimeError:
                    pass  # signal was already disconnected
                self._intelligence_worker = None

            # Re-use the already-created QSettings instance; avoid re-parsing
            # JSON on every response.
            disabled_raw = self._settings.value("intelligence/disabled_analyzers", "[]")
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

            # Set "analyzing" state AFTER the worker is created so that if
            # construction raises the panel is not permanently stuck.
            self.response_panel.intelligence_panel.set_analyzing()
            worker.start()
            self._intelligence_worker = worker
        except Exception:
            logger.debug("Intelligence analysis failed to start", exc_info=True)

    # ── History handlers ──────────────────────────────────────────────

    @staticmethod
    def _request_from_history(entry: dict) -> Request:
        """Build a Request from a history DB row."""
        return Request(
            method=entry["method"],
            url=entry["url"],
            headers=entry.get("request_headers") or {},
            body=entry.get("request_body"),
        )

    def _fetch_history_entry(self, history_id: int) -> Optional[dict]:
        """Fetch a history entry by ID, or None."""
        return HistoryManager(self.db).get_history(history_id)

    def _load_history_entry(self, history_id: int) -> None:
        self.request_panel.autosave_current()

        entry = self._fetch_history_entry(history_id)
        if not entry:
            return

        request = self._request_from_history(entry)
        self.request_panel.load_request(request)

        if entry.get("status_code"):
            raw_body = entry.get("response_body") or ""
            if isinstance(raw_body, str):
                body_bytes = raw_body.encode("utf-8")
            elif isinstance(raw_body, bytes):
                body_bytes = raw_body
            else:
                body_bytes = b""

            # Guard fromisoformat against NULL / malformed timestamps in the DB
            timestamp: Optional[datetime] = None
            try:
                executed_at = entry.get("executed_at")
                if executed_at:
                    timestamp = datetime.fromisoformat(executed_at)
            except (TypeError, ValueError):
                logger.debug("Could not parse executed_at: %s", entry.get("executed_at"))

            response = Response(
                status_code=entry["status_code"],
                reason=entry.get("reason") or "",
                headers=entry.get("response_headers") or {},
                body=body_bytes,
                elapsed=entry.get("elapsed") or 0.0,
                request=request,
                timestamp=timestamp,
            )
            self.response_panel.display_response(response)

    def _replay_history_entry(self, history_id: int) -> None:
        """Re-run a history entry exactly as originally sent."""
        self.request_panel.autosave_current()
        entry = self._fetch_history_entry(history_id)
        if not entry:
            return
        request = self._request_from_history(entry)
        self.request_panel.load_request(request)
        self.request_panel._send_request()  # noqa: SLF001

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
        new_col.triggered.connect(self.collections_panel.create_collection)
        col_menu.addAction(new_col)
        refresh_act = QAction("&Refresh", self)
        refresh_act.setShortcut("F5")
        refresh_act.triggered.connect(self.collections_panel.refresh)
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

    def _zoom_in(self) -> None:
        cur = get_font_size()
        if cur < MAX_FONT_SIZE:
            set_font_size(cur + 1)

    def _zoom_out(self) -> None:
        cur = get_font_size()
        if cur > MIN_FONT_SIZE:
            set_font_size(cur - 1)

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
        try:
            if os.name == "nt":
                os.startfile(str(log_path))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(log_path)])
            else:
                subprocess.Popen(["xdg-open", str(log_path)])
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

        _SHORTCUTS = [
            ("Ctrl+N",       "New request (clear editor)"),
            ("Ctrl+Return",  "Send request"),
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

        dialog = QDialog(self)
        dialog.setWindowTitle("Keyboard Shortcuts")
        dialog.setMinimumSize(480, 400)
        dlg_layout = QVBoxLayout(dialog)

        table = QTableWidget(len(_SHORTCUTS), 2)
        table.setHorizontalHeaderLabels(["Shortcut", "Action"])
        table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setAlternatingRowColors(True)
        for row, (shortcut, action) in enumerate(_SHORTCUTS):
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
        try:
            importer.import_file(Path(file_path))
            self.collections_panel.refresh()
            self.status_bar.showMessage(success_msg, 4000)
        except Exception as exc:
            QMessageBox.critical(self, "Import Error", f"Failed to import: {exc}")

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
        from equinox.importers.exporters import PostmanExporter, OpenAPIExporter, InsomniaExporter

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

    # ── Environment management ────────────────────────────────────────

    def _manage_environments(self) -> None:
        from equinox.gui.dialogs.environment_dialog import EnvironmentDialog
        EnvironmentDialog(self.db, self).exec()
        # Single post-exec refresh covers both changed and cancelled/dismissed.
        self.variables_panel.refresh()
        self._refresh_env_label()

    def _manage_oauth_clients(self) -> None:
        from equinox.gui.dialogs.saved_credentials_dialog import SavedCredentialsDialog
        SavedCredentialsDialog(self.db, self).exec()
