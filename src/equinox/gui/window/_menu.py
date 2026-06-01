"""Menu bar, command palette, and dialogs mixin for MainWindow."""
# mypy: disable-error-code=attr-defined
from __future__ import annotations

import logging
import os
from collections.abc import Callable
from typing import cast
from typing import TYPE_CHECKING
from typing import TypedDict

from equinox.gui.log_file_actions import show_log_file_open_result
from equinox.gui.log_file_actions import try_open_current_log_file
from equinox.gui.logging_utils import log_gui_event
from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QAction
from PyQt6.QtGui import QKeySequence
from PyQt6.QtWidgets import QDialog
from PyQt6.QtWidgets import QDialogButtonBox
from PyQt6.QtWidgets import QHeaderView
from PyQt6.QtWidgets import QLabel
from PyQt6.QtWidgets import QMenu
from PyQt6.QtWidgets import QMenuBar
from PyQt6.QtWidgets import QMessageBox
from PyQt6.QtWidgets import QPlainTextEdit
from PyQt6.QtWidgets import QTableWidget
from PyQt6.QtWidgets import QTableWidgetItem
from PyQt6.QtWidgets import QVBoxLayout
from PyQt6.QtWidgets import QWidget

if TYPE_CHECKING:
    from equinox.gui.ui_usage_tracker import UIUsageTracker

logger = logging.getLogger(__name__)

_KEY_INTEL_DISABLED = "intelligence/disabled_analyzers"
_KEY_SETUP_DONE = "onboarding/setup_wizard_completed"

# Keyboard shortcut table
_KEYBOARD_SHORTCUTS: list[tuple[str, str]] = [
    ("Ctrl+N", "New request (clear editor)"),
    ("Ctrl+L", "Focus URL field"),
    ("Ctrl+Return", "Send request"),
    ("Ctrl+S", "Save to Collection"),
    ("Ctrl+,", "Open Preferences"),
    ("Ctrl+Q", "Exit"),
    ("F5", "Refresh collections"),
    ("F1", "Keyboard Shortcuts (this dialog)"),
    ("Ctrl++", "Zoom in"),
    ("Ctrl+-", "Zoom out"),
    ("Ctrl+0", "Reset zoom"),
    ("Ctrl+Shift+F", "Format JSON body"),
    ("Ctrl+K", "Command Palette"),
    ("Ctrl+F", "Find in response body"),
    ("Ctrl+PgUp", "Previous request tab"),
    ("Ctrl+PgDn", "Next request tab"),
    ("Alt+1…Alt+6", "Switch left sidebar tabs"),
    ("F2", "Rename selected collection item"),
    ("Delete", "Delete selected collection item"),
]


class _CommandPaletteItem(TypedDict):
    """Typed payload for command-palette entries."""

    id: str
    label: str
    callback: Callable[[], None]
    shortcut: str


def _require_menu(menu: QMenu | None, label: str) -> QMenu:
    """Return a submenu and fail fast when Qt returns None."""
    if menu is None:
        raise RuntimeError(f"Failed to create menu: {label}")
    return menu


class _MenuActionsMixin:
    """Command palette, dialogs, and menu-driven action handlers."""

    def _as_qwidget(self) -> QWidget:
        """Return this mixin host as a QWidget for Qt parent arguments."""
        return cast(QWidget, self)

    def _open_preferences(self) -> None:
        from equinox.gui.dialogs.preferences_dialog import PreferencesDialog

        PreferencesDialog(self._as_qwidget()).exec()
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

        dlg = SetupWizardDialog(self._as_qwidget())
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

    def _command_palette_items(self) -> list[_CommandPaletteItem]:
        """Return command palette entries with stable IDs and callbacks."""
        commands = self._build_base_commands()

        tracker = getattr(self, "_ui_usage_tracker", None)
        if tracker is None:
            return commands

        ranked = self._rank_commands(commands, tracker)
        return [item for _, _, item in ranked]

    def _build_base_commands(self) -> list[_CommandPaletteItem]:
        return [
            self._cmd("new_request", "New Request", "Ctrl+N", self._new_request),
            self._cmd("send_request", "Send Request", "Ctrl+Enter", self.request_panel.send),
            self._cmd(
                "save_request", "Save Request", "Ctrl+S", self.request_panel.save_current_request,
            ),
            self._cmd("focus_url", "Focus URL", "Ctrl+L", self.request_panel._focus_url_input),
            self._cmd("import_postman", "Import Postman", "", self._import_postman),
            self._cmd("import_openapi", "Import OpenAPI", "", self._import_openapi),
            self._cmd("import_har", "Import HAR", "", self._import_har),
            self._cmd("import_insomnia", "Import Insomnia", "", self._import_insomnia),
            self._cmd(
                "export_postman",
                "Export Collection as Postman",
                "",
                lambda: self._export_collection("postman"),
            ),
            self._cmd(
                "export_openapi",
                "Export Collection as OpenAPI",
                "",
                lambda: self._export_collection("openapi"),
            ),
            self._cmd(
                "export_insomnia",
                "Export Collection as Insomnia",
                "",
                lambda: self._export_collection("insomnia"),
            ),
            self._cmd("manage_env", "Manage Environments", "", self._manage_environments),
            self._cmd("preferences", "Open Preferences", "Ctrl+,", self._open_preferences),
            self._cmd("setup_wizard", "Run Setup Wizard", "", self._run_setup_wizard),
        ]

    def _cmd(
        self,
        cmd_id: str,
        label: str,
        shortcut: str,
        callback: Callable[[], None],
    ) -> _CommandPaletteItem:
        return {
            "id": cmd_id,
            "label": label,
            "shortcut": shortcut,
            "callback": callback,
        }

    def _rank_commands(
        self,
        commands: list[_CommandPaletteItem],
        tracker: UIUsageTracker,
    ) -> list[tuple[int, int, _CommandPaletteItem]]:
        ranked: list[tuple[int, int, _CommandPaletteItem]] = []

        for index, cmd in enumerate(commands):
            cmd_id = str(cmd.get("id") or "")
            score = self._lookup_usage_score(tracker, cmd_id)
            ranked.append((score, index, cmd))

        ranked.sort(key=lambda item: (-item[0], item[1]))
        return ranked

    def _lookup_usage_score(self, tracker: UIUsageTracker, cmd_id: str) -> int:
        if not cmd_id:
            return 0

        count = tracker.get_count(
            category="command",
            context="command_palette",
            element_id=f"command.{cmd_id}",
        )
        return int(count)

    def _open_command_palette(self) -> None:
        """Open searchable command palette and execute selected command."""
        from equinox.gui.dialogs.command_palette_dialog import CommandPaletteDialog

        commands = self._command_palette_items()
        view_items = [
            {"id": c["id"], "label": c["label"], "shortcut": c.get("shortcut", "")}
            for c in commands
        ]
        dlg = CommandPaletteDialog(view_items, self._as_qwidget())
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
            QMessageBox.warning(
                self._as_qwidget(), "Command Failed", f"Could not execute command: {selected_id}",
            )

    def _show_shortcuts_dialog(self) -> None:
        """Display a read-only table of all keyboard shortcuts."""
        dialog = QDialog(self._as_qwidget())
        dialog.setWindowTitle("Keyboard Shortcuts")
        dialog.setMinimumSize(480, 400)
        dlg_layout = QVBoxLayout(dialog)

        table = QTableWidget(len(_KEYBOARD_SHORTCUTS), 2)
        table.setHorizontalHeaderLabels(["Shortcut", "Action"])
        h_header = table.horizontalHeader()
        if h_header is not None:
            h_header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
            h_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        v_header = table.verticalHeader()
        if v_header is not None:
            v_header.setVisible(False)
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
            self._as_qwidget(),
            "About Equinox",
            f"<h2>Equinox v{__version__}</h2>"
            "<p>A local-first API testing tool</p>"
            "<p>Built with Python and PyQt6</p>",
        )

    def _open_log_file(self) -> None:
        log_gui_event("log_file_open_requested")
        show_log_file_open_result(
            self._as_qwidget(),
            try_open_current_log_file(),
            "No log file found yet — send a request first.",
        )

    def _show_ui_usage_snapshot(self) -> None:
        tracker = getattr(self, "_ui_usage_tracker", None)
        if tracker is None:
            QMessageBox.information(
                self._as_qwidget(), "UI Usage", "Usage tracking is not available yet.",
            )
            return

        dialog = QDialog(self._as_qwidget())
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
            self._as_qwidget(),
            "Reset UI Usage Data",
            "Clear all tracked UI usage counters for this profile?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        tracker.reset()
        self.status_bar.showMessage("UI usage data reset", 3000)


class _MenuMixin(_MenuActionsMixin):
    """Menu bar construction and frameless titlebar control wiring."""

    def _create_menu_bar(self) -> None:
        """Initialize the application menu bar."""
        menubar = self.menuBar()
        if menubar is None:
            return

        menubar.addMenu(self._build_file_menu())
        menubar.addMenu(self._build_view_menu())
        menubar.addMenu(self._build_collections_menu())
        menubar.addMenu(self._build_environment_menu())
        menubar.addMenu(self._build_help_menu())

        self._add_window_controls_to_menu_bar(menubar)
        menubar.installEventFilter(self._as_qwidget())

    def _build_file_menu(self) -> QMenu:
        """Create the File menu."""
        owner = self._as_qwidget()
        menu = QMenu("&File", owner)

        new_req = QAction("&New Request", owner)
        new_req.setShortcut("Ctrl+N")
        new_req.triggered.connect(self._new_request)
        menu.addAction(new_req)
        menu.addSeparator()

        import_menu = _require_menu(menu.addMenu("&Import"), "Import")
        for label, slot in [
            ("Postman Collection…", self._import_postman),
            ("OpenAPI/Swagger Spec…", self._import_openapi),
            ("HAR File…", self._import_har),
            ("Insomnia Collection…", self._import_insomnia),
        ]:
            act = QAction(label, owner)
            act.triggered.connect(slot)
            import_menu.addAction(act)

        menu.addSeparator()

        exit_act = QAction("E&xit", owner)
        exit_act.setShortcut("Ctrl+Q")
        exit_act.triggered.connect(self.close)
        menu.addAction(exit_act)

        return menu

    def _build_view_menu(self) -> QMenu:
        """Create the View menu with zoom, theme, palette, and preferences."""
        owner = self._as_qwidget()
        menu = QMenu("&View", owner)

        for label, shortcut, slot in [
            ("Zoom &In", "Ctrl+=", self._zoom_in),
            ("Zoom &Out", "Ctrl+-", self._zoom_out),
            ("&Reset Zoom", "Ctrl+0", self._zoom_reset),
        ]:
            act = QAction(label, owner)
            act.setShortcut(QKeySequence(shortcut))
            act.triggered.connect(slot)
            menu.addAction(act)

        menu.addSeparator()
        menu.addMenu(self._build_theme_menu())
        menu.addSeparator()

        cmd_palette = QAction("Command &Palette…", owner)
        cmd_palette.setShortcut(QKeySequence("Ctrl+K"))
        cmd_palette.triggered.connect(self._open_command_palette)
        menu.addAction(cmd_palette)

        menu.addSeparator()

        prefs_act = QAction("&Preferences…", owner)
        prefs_act.setShortcut("Ctrl+,")
        prefs_act.triggered.connect(self._open_preferences)
        menu.addAction(prefs_act)

        return menu

    def _build_theme_menu(self) -> QMenu:
        """Create the theme selection submenu."""
        from equinox.gui.theme import THEME_LABELS, THEME_MODES, get_theme_mode

        owner = self._as_qwidget()
        menu = QMenu("&Theme", owner)
        self._theme_actions = {}

        current = get_theme_mode()
        for mode in THEME_MODES:
            act = QAction(str(THEME_LABELS[mode]), owner)
            act.setCheckable(True)
            act.setChecked(mode == current)
            act.triggered.connect(lambda checked, m=mode: self._set_theme(m))
            menu.addAction(act)
            self._theme_actions[mode] = act

        return menu

    def _build_collections_menu(self) -> QMenu:
        """Create the Collections menu."""
        owner = self._as_qwidget()
        menu = QMenu("&Collections", owner)

        new_col = QAction("New &Collection", owner)
        new_col.triggered.connect(
            lambda: self.collections_panel.create_collection() if self.collections_panel else None,
        )
        menu.addAction(new_col)

        refresh_act = QAction("&Refresh", owner)
        refresh_act.setShortcut("F5")
        refresh_act.triggered.connect(
            lambda: self.collections_panel.refresh() if self.collections_panel else None,
        )
        menu.addAction(refresh_act)

        menu.addSeparator()

        export_menu = _require_menu(menu.addMenu("&Export"), "Export")
        for label, fmt in [
            ("Postman Format…", "postman"),
            ("OpenAPI Format…", "openapi"),
            ("Insomnia Format…", "insomnia"),
        ]:
            act = QAction(label, owner)
            act.triggered.connect(lambda checked, f=fmt: self._export_collection(f))
            export_menu.addAction(act)

        return menu

    def _build_environment_menu(self) -> QMenu:
        """Create the Environment menu."""
        owner = self._as_qwidget()
        menu = QMenu("E&nvironment", owner)

        manage_env = QAction("&Manage Environments…", owner)
        manage_env.triggered.connect(self._manage_environments)
        menu.addAction(manage_env)

        manage_creds = QAction("Manage &Saved Credentials…", owner)
        manage_creds.triggered.connect(self._manage_oauth_clients)
        menu.addAction(manage_creds)

        manage_secrets = QAction("Manage &Secret Managers…", owner)
        manage_secrets.triggered.connect(self._manage_secret_managers)
        menu.addAction(manage_secrets)

        return menu

    def _build_help_menu(self) -> QMenu:
        """Create the Help menu."""
        owner = self._as_qwidget()
        menu = QMenu("&Help", owner)

        shortcuts_act = QAction("&Keyboard Shortcuts…", owner)
        shortcuts_act.setShortcut(QKeySequence("F1"))
        shortcuts_act.triggered.connect(self._show_shortcuts_dialog)
        menu.addAction(shortcuts_act)

        menu.addSeparator()

        log_act = QAction("&View Log File…", owner)
        log_act.triggered.connect(self._open_log_file)
        menu.addAction(log_act)

        menu.addSeparator()

        about_act = QAction("&About", owner)
        about_act.triggered.connect(self._show_about)
        menu.addAction(about_act)

        menu.addSeparator()

        setup_act = QAction("Run Setup Wizard…", owner)
        setup_act.triggered.connect(self._run_setup_wizard)
        menu.addAction(setup_act)

        menu.addSeparator()

        usage_act = QAction("UI Usage Snapshot…", owner)
        usage_act.setObjectName("help_ui_usage_snapshot")
        usage_act.triggered.connect(self._show_ui_usage_snapshot)
        menu.addAction(usage_act)

        reset_usage_act = QAction("Reset UI Usage Data", owner)
        reset_usage_act.setObjectName("help_ui_usage_reset")
        reset_usage_act.triggered.connect(self._reset_ui_usage_data)
        menu.addAction(reset_usage_act)

        return menu

    def _add_window_controls_to_menu_bar(self, menubar: QMenuBar) -> None:
        """Attach frameless-window controls to the right side of the menu bar."""
        from PyQt6.QtCore import Qt
        from PyQt6.QtWidgets import QHBoxLayout, QToolButton, QWidget

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
        owner = self._as_qwidget()
        title_container.installEventFilter(owner)
        self._menu_title_label.installEventFilter(owner)
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
