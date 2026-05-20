"""GUI prompt for entering the runtime master password."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QWidget,
)


class MasterPasswordDialog(QDialog):
    """Modal dialog that collects a master password for this app session."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Master Password")
        self.setModal(True)
        self.setMinimumWidth(420)

        layout = QFormLayout(self)
        layout.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)

        message = QLabel("Enter the master password used to decrypt encrypted secrets.")
        message.setWordWrap(True)
        layout.addRow(message)

        self._password = QLineEdit()
        self._password.setEchoMode(QLineEdit.EchoMode.Password)
        self._password.setPlaceholderText("Master password")
        self._password.returnPressed.connect(self.accept)
        layout.addRow("Password:", self._password)

        self._show_password = QCheckBox("Show password")
        self._show_password.toggled.connect(self._toggle_password_visibility)
        layout.addRow("", self._show_password)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def _toggle_password_visibility(self, checked: bool) -> None:
        mode = QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
        self._password.setEchoMode(mode)

    def password(self) -> str:
        return self._password.text()


def prompt_master_password(parent: QWidget | None = None) -> str | None:
    """Prompt for a master password and return it, or ``None`` if cancelled."""
    dialog = MasterPasswordDialog(parent)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None

    password = dialog.password()
    if not password:
        QMessageBox.warning(
            parent,
            "Master Password Required",
            "A non-empty master password is required to decrypt secrets.",
        )
        return None
    return password
