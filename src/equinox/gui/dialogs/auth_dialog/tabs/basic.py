from equinox.gui.dialogs.auth_dialog.tabs.base import AuthDialogTab
from equinox.gui.widgets import make_secret_row
from PyQt6.QtWidgets import QFormLayout
from PyQt6.QtWidgets import QLineEdit
from PyQt6.QtWidgets import QWidget

class BasicAuthTab(AuthDialogTab, QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QFormLayout(self)

        self.username = QLineEdit()
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)

        layout.addRow("Username:", self.username)
        layout.addRow("Password:", make_secret_row(self.password))

    def get_auth_config(self) -> dict[str, str]:
        return {
            "username": self.username.text().strip(),
            "password": self.password.text().strip(),
        }
