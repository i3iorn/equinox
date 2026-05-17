"""Menu bar, command palette, and dialogs mixin for MainWindow."""
from __future__ import annotations

import logging
import os

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QHeaderView,
    QLabel, QMessageBox, QPlainTextEdit, QTableWidget, QTableWidgetItem,
    QVBoxLayout,
)

from equinox.gui.log_file_actions import show_log_file_open_result, try_open_current_log_file
from equinox.gui.logging_utils import log_gui_event

logger = logging.getLogger(__name__)

_KEY_INTEL_DISABLED = "intelligence/disabled_analyzers"
_KEY_SETUP_DONE = "onboarding/setup_wizard_completed"

# Keyboard shortcut table
_KEYBOARD_SHORTCUTS: list = [
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


class _MenuMixin:
    """Menu bar construction, command palette, and top-level action handlers."""

    def _create_menu_bar(self) -> None:
        menubar = self.menuBar()
        if menubar is None:
            return

        # File menu
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

        # View menu
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

        from equinox.gui.theme import THEME_MODES, THEME_LABELS, get_theme_mode
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

        # Collections menu
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

        # Environment menu
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

        # Help menu
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
        help_menu.addSeparator()
        usage_act = QAction("UI Usage Snapshot…", self)
        usage_act.setObjectName("help_ui_usage_snapshot")
        usage_act.triggered.connect(self._show_ui_usage_snapshot)
        help_menu.addAction(usage_act)
        reset_usage_act = QAction("Reset UI Usage Data", self)
        reset_usage_act.setObjectName("help_ui_usage_reset")
        reset_usage_act.triggered.connect(self._reset_ui_usage_data)
        help_menu.addAction(reset_usage_act)

        self._add_window_controls_to_menu_bar(menubar)
        menubar.installEventFilter(self)

    def setWindowTitle(self, title: str) -> None:  # type: ignore[override]
        """Keep the menu-bar title label synchronized with the window title."""
        super().setWindowTitle(title)
        if hasattr(self, "_menu_title_label"):
            self._menu_title_label.setText(title)

    def _add_window_controls_to_menu_bar(self, menubar) -> None:
        """Attach frameless-window controls to the right side of the menu bar."""
        from PyQt6.QtCore import Qt
        from PyQt6.QtWidgets import QHBoxLayout, QToolButton, QWidget

        title_container = QWidget(menubar)
        title_container.setObjectName("menuBarWindowTitleContainer")
        title_layout = QHBoxLayout(title_container)
        title_layout.setContentsMargins(18, 0, 18, 0)

        self._menu_title_label = QLabel(self.windowTitle(), title_container)
        self._menu_title_label.setObjectName("menuBarWindowTitle")
        self._menu_title_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.NoTextInteraction
        )
        self._menu_title_label.setCursor(Qt.CursorShape.ArrowCursor)
        title_layout.addWidget(self._menu_title_label)

        menubar.setCornerWidget(title_container, Qt.Corner.TopLeftCorner)
        title_container.installEventFilter(self)
        self._menu_title_label.installEventFilter(self)
        self._drag_handles.update({menubar, title_container, self._menu_title_label})

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
        commands = [
            {"id": "new_request",    "label": "New Request",    "shortcut": "Ctrl+N",     "callback": self._new_request},
            {"id": "send_request",   "label": "Send Request",   "shortcut": "Ctrl+Enter", "callback": self.request_panel.send},
            {"id": "save_request",   "label": "Save Request",   "shortcut": "Ctrl+S",     "callback": self.request_panel.save_current_request},
            {"id": "focus_url",      "label": "Focus URL",      "shortcut": "Ctrl+L",     "callback": self.request_panel._focus_url_input},
            {"id": "import_postman", "label": "Import Postman",                            "callback": self._import_postman},
            {"id": "import_openapi", "label": "Import OpenAPI",                            "callback": self._import_openapi},
            {"id": "import_har",     "label": "Import HAR",                                "callback": self._import_har},
            {"id": "import_insomnia","label": "Import Insomnia",                           "callback": self._import_insomnia},
            {"id": "export_postman", "label": "Export Collection as Postman",              "callback": lambda: self._export_collection("postman")},
            {"id": "export_openapi", "label": "Export Collection as OpenAPI",              "callback": lambda: self._export_collection("openapi")},
            {"id": "export_insomnia","label": "Export Collection as Insomnia",             "callback": lambda: self._export_collection("insomnia")},
            {"id": "manage_env",     "label": "Manage Environments",                       "callback": self._manage_environments},
            {"id": "preferences",    "label": "Open Preferences",  "shortcut": "Ctrl+,",  "callback": self._open_preferences},
            {"id": "setup_wizard",   "label": "Run Setup Wizard",                          "callback": self._run_setup_wizard},
        ]
        tracker = getattr(self, "_ui_usage_tracker", None)
        if tracker is None:
            return commands

        ranked: list = []
        for idx, cmd in enumerate(commands):
            cmd_id = str(cmd.get("id") or "")
            score = 0
            if cmd_id:
                score = tracker.get_count(
                    category="command",
                    context="command_palette",
                    element_id=f"command.{cmd_id}",
                )
            ranked.append((score, idx, cmd))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        return [item[2] for item in ranked]

    def _open_command_palette(self) -> None:
        """Open searchable command palette and execute selected command."""
        from equinox.gui.dialogs.command_palette_dialog import CommandPaletteDialog

        commands = self._command_palette_items()
        view_items = [
            {"id": c["id"], "label": c["label"], "shortcut": c.get("shortcut", "")}
            for c in commands
        ]
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
                tracker = getattr(self, "_ui_usage_tracker", None)
                if tracker is not None:
                    tracker.record(
                        f"command.{selected_id}",
                        category="command",
                        context="command_palette",
                    )
        except Exception:
            logger.error("Command palette command failed: %s", selected_id, exc_info=True)
            QMessageBox.warning(self, "Command Failed", f"Could not execute command: {selected_id}")

    def _show_shortcuts_dialog(self) -> None:
        """Display a read-only table of all keyboard shortcuts."""
        dialog = QDialog(self)
        dialog.setWindowTitle("Keyboard Shortcuts")
        dialog.setMinimumSize(480, 400)
        dlg_layout = QVBoxLayout(dialog)

        table = QTableWidget(len(_KEYBOARD_SHORTCUTS), 2)
        table.setHorizontalHeaderLabels(["Shortcut", "Action"])
        table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setAlternatingRowColors(True)
        for row, (shortcut, action) in enumerate(_KEYBOARD_SHORTCUTS):
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

    def _open_log_file(self) -> None:
        log_gui_event("log_file_open_requested")
        show_log_file_open_result(
            self,
            try_open_current_log_file(),
            "No log file found yet — send a request first.",
        )

    def _show_ui_usage_snapshot(self) -> None:
        tracker = getattr(self, "_ui_usage_tracker", None)
        if tracker is None:
            QMessageBox.information(self, "UI Usage", "Usage tracking is not available yet.")
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("UI Usage Snapshot")
        dialog.setMinimumSize(680, 420)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("Local usage counts from your current Equinox profile."))

        text = QPlainTextEdit(dialog)
        text.setReadOnly(True)
        text.setPlainText(tracker.snapshot_text())
        layout.addWidget(text, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    def _reset_ui_usage_data(self) -> None:
        tracker = getattr(self, "_ui_usage_tracker", None)
        if tracker is None:
            return
        answer = QMessageBox.question(
            self,
            "Reset UI Usage Data",
            "Clear all tracked UI usage counters for this profile?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        tracker.reset()
        self.status_bar.showMessage("UI usage data reset", 3000)

