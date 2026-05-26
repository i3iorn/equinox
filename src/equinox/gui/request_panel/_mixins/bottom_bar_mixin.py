from __future__ import annotations

from typing import Callable, Tuple

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QToolButton,
)

from equinox.gui.request_panel._constants import SEND_BTN_WIDTH


class BottomBarMixin:
    """Mixin providing a deterministic, testable bottom toolbar builder."""

    # The parent class must define this signal.
    session_vars_changed: pyqtSignal

    # -----------------------------
    # Public API
    # -----------------------------
    def build_bottom_bar(self) -> QHBoxLayout:
        """Create the bottom toolbar with save, secondary tools, and session info."""
        layout = self._create_base_layout()
        save_button = self._create_save_button()
        more_button = self._create_secondary_tools_button()

        editor_state_label = self._create_editor_state_label()
        session_label = self._create_session_vars_label()

        layout.addWidget(save_button)
        layout.addWidget(more_button)
        layout.addStretch()
        layout.addWidget(editor_state_label)
        layout.addWidget(session_label)

        self._connect_session_var_updates(session_label)
        return layout

    # -----------------------------
    # Layout Construction
    # -----------------------------
    def _create_base_layout(self) -> QHBoxLayout:
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        return layout

    # -----------------------------
    # Save Button
    # -----------------------------
    def _create_save_button(self) -> QPushButton:
        button = QPushButton("Save")
        button.setObjectName("requestSaveBtn")
        button.setProperty("usage_track_id", "request.save")
        button.setMinimumWidth(SEND_BTN_WIDTH)
        button.setToolTip("Save to a collection (prompts for name / folder)")
        button.clicked.connect(self._save_request)
        self.save_button = button
        return button

    # -----------------------------
    # Secondary Tools
    # -----------------------------
    def _create_secondary_tools_button(self) -> QToolButton:
        button = QToolButton()
        button.setText("More ▾")
        button.setToolTip("Secondary request tools")
        button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        button.setObjectName("requestMoreToolsBtn")
        button.setProperty("usage_track_id", "request.more_tools")

        menu = self._create_secondary_tools_menu()
        button.setMenu(menu)
        return button

    def _create_secondary_tools_menu(self) -> QMenu:
        menu = QMenu()

        actions = [
            self._create_menu_action(
                text="Import from cURL…",
                object_name="request_import_curl",
                usage_id="request.import_curl",
                handler=self._import_from_curl,
                is_destructive=False,
            ),
            self._create_menu_action(
                text="Benchmark…",
                object_name="request_benchmark",
                usage_id="request.benchmark",
                handler=self._open_benchmark,
                is_destructive=False,
            ),
            self._create_menu_action(
                text="Clear Session Vars",
                object_name="request_clear_session_vars",
                usage_id="request.clear_session_vars",
                handler=self.clear_session_vars,
                is_destructive=True,
            ),
        ]

        self._secondary_tool_actions = actions
        self._secondary_tools_menu = menu

        self._rebuild_secondary_tools_menu()
        menu.aboutToShow.connect(self._rebuild_secondary_tools_menu)

        return menu

    def _create_menu_action(
        self,
        *,
        text: str,
        object_name: str,
        usage_id: str,
        handler: Callable,
        is_destructive: bool,
    ) -> Tuple[QAction, bool]:
        action = QAction(text, self)
        action.setObjectName(object_name)
        action.setProperty("usage_track_id", usage_id)
        action.triggered.connect(handler)
        return action, is_destructive

    def _rebuild_secondary_tools_menu(self) -> None:
        menu: QMenu = self._secondary_tools_menu
        menu.clear()

        for action, is_destructive in self._secondary_tool_actions:
            if is_destructive:
                action.setProperty("is_destructive", True)
            menu.addAction(action)

    # -----------------------------
    # Labels
    # -----------------------------
    def _create_session_vars_label(self) -> QLabel:
        label = QLabel("Session vars: 0")
        label.setObjectName("mutedLabel")
        self._session_vars_label = label
        return label

    def _create_editor_state_label(self) -> QLabel:
        label = QLabel("Scratch request")
        label.setObjectName("mutedLabel")
        self._editor_state_label = label
        return label

    # -----------------------------
    # Reactive Updates
    # -----------------------------
    def _connect_session_var_updates(self, label: QLabel) -> None:
        def update_label(session_vars: dict) -> None:
            count = len(session_vars)
            label.setText(f"Session vars: {count}")

        self.session_vars_changed.connect(update_label)
