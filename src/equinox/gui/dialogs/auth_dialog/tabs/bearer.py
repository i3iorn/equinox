from equinox.gui.dialogs.auth_dialog.tabs.base import AuthDialogTab
from equinox.gui.widgets import make_secret_row
from PyQt6.QtWidgets import QFormLayout
from PyQt6.QtWidgets import QLineEdit
from PyQt6.QtWidgets import QWidget


class BearerAuthTab(AuthDialogTab, QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QFormLayout(self)

        self.token = QLineEdit()
        self.token.setEchoMode(QLineEdit.EchoMode.Password)
        self.token.setPlaceholderText("Paste your bearer token…")

        layout.addRow("Token:", make_secret_row(self.token))

    def get_auth_config(self) -> dict[str, str]:
        return {"token": self.token.text().strip()}
