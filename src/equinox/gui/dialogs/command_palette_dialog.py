"""Command palette dialog for fast keyboard-driven actions."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import (
    QDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)


class CommandPaletteDialog(QDialog):
    """Simple searchable command picker."""

    def __init__(self, commands: list[dict[str, str]], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Command Palette")
        self.setMinimumSize(520, 380)

        self._commands = commands
        self._selected_id: str | None = None

        layout = QVBoxLayout(self)
        self._search = QLineEdit()
        self._search.setPlaceholderText("Type a command name or shortcut...")
        self._search.textChanged.connect(self._rebuild_list)
        layout.addWidget(self._search)

        self._list = QListWidget()
        self._list.itemActivated.connect(self._accept_item)
        self._list.itemDoubleClicked.connect(self._accept_item)
        layout.addWidget(self._list, 1)

        hint = QLabel("Enter to run, Esc to close")
        hint.setObjectName("mutedLabel")
        layout.addWidget(hint)

        self._rebuild_list("")
        self._search.setFocus(Qt.FocusReason.ActiveWindowFocusReason)

    def _command_label(self, cmd: dict[str, str]) -> str:
        shortcut = (cmd.get("shortcut") or "").strip()
        if shortcut:
            return f"{cmd['label']}    [{shortcut}]"
        return cmd["label"]

    def _rebuild_list(self, text: str) -> None:
        term = (text or "").strip().lower()
        self._list.clear()
        for cmd in self._commands:
            haystack = f"{cmd.get('label', '')} {cmd.get('shortcut', '')}".lower()
            if term and term not in haystack:
                continue
            item = QListWidgetItem(self._command_label(cmd))
            item.setData(Qt.ItemDataRole.UserRole, cmd.get("id"))
            self._list.addItem(item)

        if self._list.count() > 0:
            self._list.setCurrentRow(0)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # type: ignore[override]
        if event.key() in (Qt.Key.Key_Down, Qt.Key.Key_Up):
            self._list.setFocus(Qt.FocusReason.ShortcutFocusReason)
            return super().keyPressEvent(event)
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            current = self._list.currentItem()
            if current is not None:
                self._accept_item(current)
                return
        super().keyPressEvent(event)

    def _accept_item(self, item: QListWidgetItem) -> None:
        self._selected_id = item.data(Qt.ItemDataRole.UserRole)
        self.accept()

    def selected_command_id(self) -> str | None:
        return self._selected_id
