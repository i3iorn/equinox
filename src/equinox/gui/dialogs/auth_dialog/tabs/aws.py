from equinox.gui.dialogs.auth_dialog.tabs.base import AuthDialogTab
from equinox.gui.widgets import make_secret_row
from PyQt6.QtWidgets import QFormLayout
from PyQt6.QtWidgets import QLineEdit
from PyQt6.QtWidgets import QWidget

class AwsSigV4AuthTab(AuthDialogTab, QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QFormLayout(self)

        self.access_key = QLineEdit()
        self.secret_key = QLineEdit()
        self.secret_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.region = QLineEdit()
        self.service = QLineEdit()
        self.session_token = QLineEdit()
        self.session_token.setEchoMode(QLineEdit.EchoMode.Password)

        layout.addRow("Access Key ID:", self.access_key)
        layout.addRow("Secret Access Key:", make_secret_row(self.secret_key))
        layout.addRow("Region:", self.region)
        layout.addRow("Service:", self.service)
        layout.addRow("Session Token:", make_secret_row(self.session_token))

    def get_auth_config(self) -> dict[str, str | None]:
        return {
            "access_key": self.access_key.text().strip(),
            "secret_key": self.secret_key.text().strip(),
            "region": self.region.text().strip(),
            "service": self.service.text().strip(),
            "session_token": self.session_token.text().strip() or None,
        }
