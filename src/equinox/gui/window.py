"""Main window for Equinox GUI"""

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QTabWidget, QStatusBar, QPushButton,
    QMessageBox,
)
from PyQt6.QtCore import Qt, QTimer, QSettings, QByteArray
from PyQt6.QtGui import QAction, QKeySequence

from equinox.storage import Database
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
    get_theme_mode, set_theme_mode, THEME_MODES, THEME_LABELS,
)

_SETTINGS_KEY = "Equinox"


class MainWindow(QMainWindow):
    """Main application window"""

    def __init__(self, db: Database):
        super().__init__()
        self.db = db
        self._settings = QSettings(_SETTINGS_KEY, _SETTINGS_KEY)
        self.setWindowTitle("Equinox — API Testing")
        self.setGeometry(100, 100, 1400, 900)

        self._init_ui()
        self._create_menu_bar()
        self._create_status_bar()
        self._restore_layout()

    # ── Layout persistence (#1) ───────────────────────────────────────

    def _restore_layout(self):
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
                pass
        rs = self._settings.value("splitter/req_resp")
        if rs is not None:
            try:
                self._req_resp_splitter.setSizes([int(x) for x in rs])
            except Exception:
                pass
        tab_idx = self._settings.value("left_tabs/index", 0, type=int)
        self._left_tabs.setCurrentIndex(tab_idx)

    def _save_layout(self):
        """Persist window geometry and splitter sizes."""
        self._settings.setValue("window/geometry", self.saveGeometry())
        self._settings.setValue("window/state", self.saveState())
        self._settings.setValue("splitter/main", self._main_splitter.sizes())
        self._settings.setValue("splitter/req_resp", self._req_resp_splitter.sizes())
        self._settings.setValue("left_tabs/index", self._left_tabs.currentIndex())

    def closeEvent(self, event):
        # Autosave any pending edits before closing
        self.request_panel.autosave_current()
        self._save_layout()
        super().closeEvent(event)

    def _init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        main_layout = QHBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)

        self._main_splitter = QSplitter(Qt.Orientation.Horizontal)

        self._cookie_manager = CookieJarManager(self.db)

        self._left_tabs = QTabWidget()
        self.collections_panel = CollectionsPanel(self.db, self)
        self.history_panel = HistoryPanel(self.db, self)
        self.variables_panel = VariablesPanel(self.db, self)
        self.logging_panel = LoggingPanel(self)
        self.cookies_panel = CookiesPanel(self.db, self)
        self.websocket_panel = WebSocketPanel(self)
        self._left_tabs.addTab(self.collections_panel, "Collections")
        self._left_tabs.addTab(self.history_panel, "History")
        self._left_tabs.addTab(self.variables_panel, "Variables")
        self._left_tabs.addTab(self.logging_panel, "Logs")
        self._left_tabs.addTab(self.cookies_panel, "Cookies")
        self._left_tabs.addTab(self.websocket_panel, "WebSocket")
        self._left_tabs.setMinimumWidth(180)

        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)

        self._req_resp_splitter = QSplitter(Qt.Orientation.Vertical)
        self.request_panel = RequestPanel(self.db, self, cookie_manager=self._cookie_manager)
        self.response_panel = ResponsePanel(self)
        # Allow both panels to shrink so the splitter handle is always draggable.
        # Without an explicit minimum the scripts-tab content inflates the request
        # panel's minimum height to ~400 px, leaving no room for the splitter to move.
        self.request_panel.setMinimumHeight(180)
        self.response_panel.setMinimumHeight(120)
        self._req_resp_splitter.addWidget(self.request_panel)
        self._req_resp_splitter.addWidget(self.response_panel)
        self._req_resp_splitter.setSizes([400, 500])
        self._req_resp_splitter.setChildrenCollapsible(False)
        self._req_resp_splitter.setHandleWidth(5)
        right_layout.addWidget(self._req_resp_splitter)

        self._main_splitter.addWidget(self._left_tabs)
        self._main_splitter.addWidget(right_widget)
        self._main_splitter.setSizes([300, 1100])
        self._main_splitter.setStretchFactor(0, 0)   # left panel: fixed on window resize
        self._main_splitter.setStretchFactor(1, 1)   # right panel: absorbs extra space
        self._main_splitter.setChildrenCollapsible(False)
        self._main_splitter.setHandleWidth(5)
        main_layout.addWidget(self._main_splitter)

        # ── Signal wiring ─────────────────────────────────────────────
        self.request_panel.response_received.connect(self.response_panel.display_response)
        self.request_panel.response_received.connect(self._on_response_received)
        self.collections_panel.request_selected.connect(self._load_request_guarded)
        self.collections_panel.request_run.connect(self._run_request_directly)
        self.history_panel.history_selected.connect(self._load_history_entry)
        self.history_panel.history_replay.connect(self._replay_history_entry)

        # Refresh cookies panel after every response (server may set cookies)
        self.request_panel.response_received.connect(
            lambda _r: self.cookies_panel.refresh())

        # #5 — Signal-driven refresh instead of constant polling
        self.request_panel.response_received.connect(
            lambda _r: self.history_panel.refresh())
        self.collections_panel.collections_changed.connect(
            lambda: self.collections_panel.refresh())
        # When collection/folder auth changes, refresh the request panel's
        # inherited auth so the display and send-time resolution stay current.
        self.collections_panel.collections_changed.connect(
            self.request_panel.refresh_inherited_auth)

        # Session variables: captured values flow into the Variables panel
        self.request_panel.session_vars_changed.connect(
            self.variables_panel.refresh_session_vars)
        self.variables_panel.clear_session_requested.connect(
            self.request_panel.clear_session_vars)

    def _load_request_guarded(self, request):
        """Auto-save current request then load the new one."""
        self.request_panel.autosave_current()
        self.request_panel.load_request(request)

    def _on_response_received(self, response):
        """Update status bar with timing info and refresh history after a response."""
        try:
            code = response.status_code
            elapsed_ms = int(response.elapsed * 1000)
            size = response.size
            # Format size
            for unit in ("B", "KB", "MB", "GB"):
                if size < 1024.0:
                    size_str = f"{size:.1f} {unit}"
                    break
                size /= 1024.0
            else:
                size_str = f"{size:.1f} TB"
            self.status_bar.showMessage(
                f"{code} {response.reason}  ·  {elapsed_ms} ms  ·  {size_str}", 10_000
            )
        except Exception:
            pass
        try:
            self.history_panel.refresh()
        except Exception:
            pass

    def _run_request_directly(self, request):
        """Load a request into the editor then fire it immediately."""
        self.request_panel.autosave_current()
        self.request_panel.load_request(request)
        self.request_panel._send_request()

    def _new_request(self):
        """Autosave current request then clear the editor for a new one."""
        self.request_panel.autosave_current()
        self.request_panel.clear()

    # ── Menu bar ──────────────────────────────────────────────────────

    def _create_menu_bar(self):
        menubar = self.menuBar()

        file_menu = menubar.addMenu("&File")
        new_request_action = QAction("&New Request", self)
        new_request_action.setShortcut("Ctrl+N")
        new_request_action.triggered.connect(self._new_request)
        file_menu.addAction(new_request_action)
        file_menu.addSeparator()

        import_menu = file_menu.addMenu("&Import")
        import_postman_action = QAction("Postman Collection…", self)
        import_postman_action.triggered.connect(self._import_postman)
        import_menu.addAction(import_postman_action)
        import_openapi_action = QAction("OpenAPI/Swagger Spec…", self)
        import_openapi_action.triggered.connect(self._import_openapi)
        import_menu.addAction(import_openapi_action)
        import_har_action = QAction("HAR File…", self)
        import_har_action.triggered.connect(self._import_har)
        import_menu.addAction(import_har_action)
        import_insomnia_action = QAction("Insomnia Collection…", self)
        import_insomnia_action.triggered.connect(self._import_insomnia)
        import_menu.addAction(import_insomnia_action)
        file_menu.addSeparator()

        exit_action = QAction("E&xit", self)
        exit_action.setShortcut("Ctrl+Q")
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        # ── View menu (zoom + preferences) ────────────────────────────
        view_menu = menubar.addMenu("&View")

        zoom_in_action = QAction("Zoom &In", self)
        zoom_in_action.setShortcut(QKeySequence("Ctrl+="))
        zoom_in_action.triggered.connect(self._zoom_in)
        view_menu.addAction(zoom_in_action)

        zoom_out_action = QAction("Zoom &Out", self)
        zoom_out_action.setShortcut(QKeySequence("Ctrl+-"))
        zoom_out_action.triggered.connect(self._zoom_out)
        view_menu.addAction(zoom_out_action)

        zoom_reset_action = QAction("&Reset Zoom", self)
        zoom_reset_action.setShortcut(QKeySequence("Ctrl+0"))
        zoom_reset_action.triggered.connect(self._zoom_reset)
        view_menu.addAction(zoom_reset_action)

        view_menu.addSeparator()

        # Theme submenu
        theme_menu = view_menu.addMenu("&Theme")
        self._theme_actions = {}
        for mode in THEME_MODES:
            a = QAction(THEME_LABELS[mode], self)
            a.setCheckable(True)
            a.setChecked(mode == get_theme_mode())
            a.triggered.connect(lambda checked, m=mode: self._set_theme(m))
            theme_menu.addAction(a)
            self._theme_actions[mode] = a

        view_menu.addSeparator()

        preferences_action = QAction("&Preferences…", self)
        preferences_action.setShortcut("Ctrl+,")
        preferences_action.triggered.connect(self._open_preferences)
        view_menu.addAction(preferences_action)

        collections_menu = menubar.addMenu("&Collections")
        new_collection_action = QAction("New &Collection", self)
        new_collection_action.triggered.connect(self.collections_panel.create_collection)
        collections_menu.addAction(new_collection_action)
        refresh_action = QAction("&Refresh", self)
        refresh_action.setShortcut("F5")
        refresh_action.triggered.connect(self.collections_panel.refresh)
        collections_menu.addAction(refresh_action)
        collections_menu.addSeparator()
        export_menu = collections_menu.addMenu("&Export")
        for label, fmt in [("Postman Format…", "postman"),
                           ("OpenAPI Format…", "openapi"),
                           ("Insomnia Format…", "insomnia")]:
            a = QAction(label, self)
            a.triggered.connect(lambda checked, f=fmt: self._export_collection(f))
            export_menu.addAction(a)

        env_menu = menubar.addMenu("E&nvironment")
        manage_env_action = QAction("&Manage Environments…", self)
        manage_env_action.triggered.connect(self._manage_environments)
        env_menu.addAction(manage_env_action)

        manage_oauth_action = QAction("Manage &Saved Credentials…", self)
        manage_oauth_action.triggered.connect(self._manage_oauth_clients)
        env_menu.addAction(manage_oauth_action)

        help_menu = menubar.addMenu("&Help")
        shortcuts_action = QAction("&Keyboard Shortcuts…", self)
        shortcuts_action.setShortcut(QKeySequence("F1"))
        shortcuts_action.triggered.connect(self._show_shortcuts_dialog)
        help_menu.addAction(shortcuts_action)
        help_menu.addSeparator()
        view_log_action = QAction("&View Log File…", self)
        view_log_action.triggered.connect(self._open_log_file)
        help_menu.addAction(view_log_action)
        help_menu.addSeparator()
        about_action = QAction("&About", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)

    # ── Zoom ──────────────────────────────────────────────────────────

    def _zoom_in(self):
        cur = get_font_size()
        if cur < MAX_FONT_SIZE:
            set_font_size(cur + 1)

    def _zoom_out(self):
        cur = get_font_size()
        if cur > MIN_FONT_SIZE:
            set_font_size(cur - 1)

    def _zoom_reset(self):
        from equinox.gui.theme import DEFAULT_FONT_SIZE
        set_font_size(DEFAULT_FONT_SIZE)

    def _set_theme(self, mode: str):
        set_theme_mode(mode)
        self._sync_theme_checks()

    def _sync_theme_checks(self):
        """Keep the theme radio-check marks in sync with the current mode."""
        current = get_theme_mode()
        for mode, action in self._theme_actions.items():
            action.setChecked(mode == current)

    def _open_preferences(self):
        from equinox.gui.dialogs.preferences_dialog import PreferencesDialog
        PreferencesDialog(self).exec()
        self._sync_theme_checks()

    # ── Status bar ────────────────────────────────────────────────────

    def _create_status_bar(self):
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)

        # Environment quick-switch: click label to open dropdown, ⚙ to manage
        from PyQt6.QtWidgets import QToolButton, QMenu
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

        # Lazy fallback timer (30 s) — immediate refresh via signals
        self._env_timer = QTimer(self)
        self._env_timer.setInterval(30_000)
        self._env_timer.timeout.connect(self._refresh_env_label)
        self._env_timer.start()

        self.status_bar.showMessage("Ready")

    def _show_env_menu(self) -> None:
        """Build and show the environment quick-switch menu."""
        from PyQt6.QtWidgets import QMenu
        from equinox.storage import EnvironmentManager
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

            manage_action = self._env_menu.addAction("Manage Environments…")
            manage_action.triggered.connect(self._manage_environments)
        except Exception:
            manage_action = self._env_menu.addAction("Manage Environments…")
            manage_action.triggered.connect(self._manage_environments)

        self._env_menu.popup(
            self._env_btn.mapToGlobal(self._env_btn.rect().topLeft())
        )

    def _switch_environment(self, env_id: int) -> None:
        """Activate the given environment and refresh the label."""
        try:
            from equinox.storage import EnvironmentManager
            EnvironmentManager(self.db).set_active_environment(env_id)
            self._refresh_env_label()
            self.request_panel.refresh_inherited_auth()
        except Exception:
            pass

    def _refresh_env_label(self):
        try:
            from equinox.storage import EnvironmentManager
            mgr = EnvironmentManager(self.db)
            env = mgr.get_active_environment()
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
            pass

    # ── History handlers ──────────────────────────────────────────────

    @staticmethod
    def _request_from_history(entry: dict):
        """Build a Request from a history DB row."""
        from equinox.core.request import Request
        return Request(
            method=entry["method"],
            url=entry["url"],
            headers=entry.get("request_headers") or {},
            body=entry.get("request_body"),
        )

    def _fetch_history_entry(self, history_id: int):
        """Fetch a history entry by ID, or None."""
        from equinox.storage import HistoryManager
        return HistoryManager(self.db).get_history(history_id)

    def _load_history_entry(self, history_id: int):
        from equinox.core.request import Response
        from datetime import datetime

        # Autosave current request before switching
        self.request_panel.autosave_current()

        entry = self._fetch_history_entry(history_id)
        if not entry:
            return

        request = self._request_from_history(entry)
        self.request_panel.load_request(request)

        if entry.get("status_code"):
            # response_body is stored as text in the DB; Response.body expects bytes
            raw_body = entry.get("response_body") or ""
            if isinstance(raw_body, str):
                body_bytes = raw_body.encode("utf-8")
            elif isinstance(raw_body, bytes):
                body_bytes = raw_body
            else:
                body_bytes = b""
            response = Response(
                status_code=entry["status_code"],
                reason=entry.get("reason") or "",
                headers=entry.get("response_headers") or {},
                body=body_bytes,
                elapsed=entry.get("elapsed") or 0.0,
                request=request,
                timestamp=datetime.fromisoformat(entry["executed_at"]),
            )
            self.response_panel.display_response(response)

    def _replay_history_entry(self, history_id: int):
        """Re-run a history entry exactly as originally sent."""
        self.request_panel.autosave_current()
        entry = self._fetch_history_entry(history_id)
        if not entry:
            return

        request = self._request_from_history(entry)
        self.request_panel.load_request(request)
        self.request_panel._send_request()

    # ── Log file ──────────────────────────────────────────────────────

    def _open_log_file(self):
        import subprocess, os
        from equinox.core.log_setup import get_log_file
        from PyQt6.QtWidgets import QMessageBox
        log_path = get_log_file()
        if not log_path or not log_path.exists():
            QMessageBox.information(self, "Log File",
                "No log file found yet — send a request first.")
            return
        try:
            if os.name == "nt":
                os.startfile(str(log_path))
            elif os.path.exists("/usr/bin/open"):
                subprocess.Popen(["open", str(log_path)])
            else:
                subprocess.Popen(["xdg-open", str(log_path)])
        except Exception as exc:
            QMessageBox.information(self, "Log File",
                f"Log file:\n{log_path}\n\n(Could not open automatically: {exc})")

    def _show_shortcuts_dialog(self) -> None:
        """Display a read-only table of all keyboard shortcuts."""
        from PyQt6.QtWidgets import (
            QDialog, QVBoxLayout, QTableWidget, QTableWidgetItem,
            QHeaderView, QDialogButtonBox,
        )

        _SHORTCUTS = [
            ("Ctrl+N",          "New request (clear editor)"),
            ("Ctrl+Return",     "Send request"),
            ("Ctrl+,",          "Open Preferences"),
            ("Ctrl+Q",          "Exit"),
            ("F5",              "Refresh collections"),
            ("F1",              "Keyboard Shortcuts (this dialog)"),
            ("Ctrl+=",          "Zoom in"),
            ("Ctrl+-",          "Zoom out"),
            ("Ctrl+0",          "Reset zoom"),
            ("Ctrl+Shift+F",    "Format JSON body"),
            ("Ctrl+F",          "Find in response body"),
            ("F2",              "Rename selected collection item"),
            ("Delete",          "Delete selected collection item"),
        ]

        dialog = QDialog(self)
        dialog.setWindowTitle("Keyboard Shortcuts")
        dialog.setMinimumSize(480, 400)
        dlg_layout = QVBoxLayout(dialog)

        table = QTableWidget(len(_SHORTCUTS), 2)
        table.setHorizontalHeaderLabels(["Shortcut", "Action"])
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
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

    def _show_about(self):
        from PyQt6.QtWidgets import QMessageBox
        from equinox import __version__
        QMessageBox.about(self, "About Equinox",
            f"<h2>Equinox v{__version__}</h2>"
            "<p>A local-first API testing tool</p>"
            "<p>Built with Python and PyQt6</p>")

    # ── Import / Export ───────────────────────────────────────────────

    def _import_with(self, importer_class, dialog_title: str, file_filter: str, success_msg: str):
        """Generic import handler — opens a file dialog, runs the importer, refreshes."""
        from PyQt6.QtWidgets import QFileDialog, QMessageBox
        from equinox.storage import CollectionManager
        from pathlib import Path

        file_path, _ = QFileDialog.getOpenFileName(self, dialog_title, "", file_filter)
        if not file_path:
            return
        collection_manager = CollectionManager(self.db)
        importer = importer_class(collection_manager)
        try:
            importer.import_file(Path(file_path))
            self.collections_panel.refresh()
            self.status_bar.showMessage(success_msg, 4000)
        except Exception as exc:
            QMessageBox.critical(self, "Import Error", f"Failed to import: {exc}")

    def _import_postman(self):
        from equinox.importers import PostmanImporter
        self._import_with(
            PostmanImporter,
            "Import Postman Collection",
            "JSON Files (*.json);;All Files (*)",
            "Postman collection imported",
        )

    def _import_openapi(self):
        from equinox.importers import OpenAPIImporter
        self._import_with(
            OpenAPIImporter,
            "Import OpenAPI/Swagger Specification",
            "API Spec Files (*.json *.yaml *.yml);;All Files (*)",
            "OpenAPI specification imported",
        )

    def _import_har(self):
        from equinox.importers import HARImporter
        self._import_with(
            HARImporter,
            "Import HAR File",
            "HAR Files (*.har);;JSON Files (*.json);;All Files (*)",
            "HAR file imported",
        )

    def _import_insomnia(self):
        from equinox.importers import InsomniaImporter
        self._import_with(
            InsomniaImporter,
            "Import Insomnia Collection",
            "JSON Files (*.json);;All Files (*)",
            "Insomnia collection imported",
        )

    def _export_collection(self, format_type: str):
        from PyQt6.QtWidgets import QFileDialog, QMessageBox, QInputDialog
        from equinox.importers.exporters import PostmanExporter, OpenAPIExporter, InsomniaExporter
        from equinox.storage import CollectionManager
        from pathlib import Path

        mgr = CollectionManager(self.db)
        collections = mgr.list_collections()
        if not collections:
            QMessageBox.warning(self, "No Collections", "No collections to export.")
            return

        col_names = [col['name'] for col in collections]
        col_name, ok = QInputDialog.getItem(
            self, "Select Collection", "Choose collection to export:", col_names, 0, False)
        if not ok:
            return
        collection_id = next(c['id'] for c in collections if c['name'] == col_name)

        file_path, _ = QFileDialog.getSaveFileName(
            self, f"Export as {format_type.title()}", "", "JSON Files (*.json)")
        if not file_path:
            return

        try:
            if format_type == "postman":
                data = PostmanExporter.export_collection(self.db, collection_id)
                PostmanExporter.export_to_file(data, Path(file_path))
            elif format_type == "openapi":
                title, ok = QInputDialog.getText(self, "OpenAPI Title", "API Title:", text=col_name)
                if not ok:
                    return
                data = OpenAPIExporter.export_collection(self.db, collection_id, title)
                OpenAPIExporter.export_to_file(data, Path(file_path))
            elif format_type == "insomnia":
                data = InsomniaExporter.export_collection(self.db, collection_id)
                InsomniaExporter.export_to_file(data, Path(file_path))

            self.status_bar.showMessage(f"Exported to {file_path}", 5000)
        except Exception as e:
            QMessageBox.critical(self, "Export Error", f"Failed to export: {e}")

    def _manage_environments(self):
        from equinox.gui.dialogs.environment_dialog import EnvironmentDialog
        dialog = EnvironmentDialog(self.db, self)
        dialog.environment_changed.connect(self._refresh_env_label)
        dialog.exec()
        self.variables_panel.refresh()
        self._refresh_env_label()

    def _manage_oauth_clients(self):
        from equinox.gui.dialogs.saved_credentials_dialog import SavedCredentialsDialog
        dialog = SavedCredentialsDialog(self.db, self)
        dialog.exec()

