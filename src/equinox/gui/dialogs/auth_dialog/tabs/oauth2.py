from equinox.gui.dialogs.auth_dialog.tabs.base import AuthDialogTab
from equinox.gui.widgets import make_secret_row
from PyQt6.QtWidgets import QCheckBox
from PyQt6.QtWidgets import QComboBox
from PyQt6.QtWidgets import QFormLayout
from PyQt6.QtWidgets import QHBoxLayout
from PyQt6.QtWidgets import QLabel
from PyQt6.QtWidgets import QLineEdit
from PyQt6.QtWidgets import QPushButton
from PyQt6.QtWidgets import QWidget

class OAuth2AuthTab(AuthDialogTab, QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        layout = QFormLayout(self)

        self.token_url = QLineEdit()
        self.client_id = QLineEdit()
        self.client_secret = QLineEdit()
        self.client_secret.setEchoMode(QLineEdit.EchoMode.Password)
        self.scope = QLineEdit()
        self.access_token = QLineEdit()
        self.access_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.refresh_token = QLineEdit()
        self.refresh_token.setEchoMode(QLineEdit.EchoMode.Password)

        self.token_auth = QComboBox()
        self.token_auth.addItem("Body", userData="body")
        self.token_auth.addItem("HTTP Basic", userData="basic")

        self.verify_ssl = QCheckBox("Verify SSL")
        self.verify_ssl.setChecked(True)

        layout.addRow("Token URL:*", self.token_url)
        layout.addRow("Client ID:*", self.client_id)
        layout.addRow("Client Secret:", make_secret_row(self.client_secret))
        layout.addRow("Scope:", self.scope)
        layout.addRow("Client Auth:", self.token_auth)
        layout.addRow("", self.verify_ssl)
        layout.addRow("Access Token:", make_secret_row(self.access_token))
        layout.addRow("Refresh Token:", make_secret_row(self.refresh_token))

        # Buttons (controller wires these)
        self.fetch_btn = QPushButton("Fetch Token…")
        self.view_btn = QPushButton("View Response…")
        self.view_btn.setEnabled(False)

        row = QHBoxLayout()
        row.addWidget(self.fetch_btn)
        row.addWidget(self.view_btn)
        row.addStretch()
        layout.addRow("", row)

        self.status = QLabel("")
        layout.addRow(self.status)

    def get_auth_config(self) -> dict[str, str | bool | None]:
        return {
            "token_url": self.token_url.text().strip(),
            "client_id": self.client_id.text().strip(),
            "client_secret": self.client_secret.text().strip() or None,
            "scope": self.scope.text().strip() or None,
            "verify_ssl": self.verify_ssl.isChecked(),
            "token_auth": self.token_auth.currentData(),
            "access_token": self.access_token.text().strip() or None,
            "refresh_token": self.refresh_token.text().strip() or None,
        }
