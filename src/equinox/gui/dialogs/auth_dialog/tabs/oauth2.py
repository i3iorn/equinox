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

        self.token_field_name = QLineEdit("access_token")
        self.token_field_name.setPlaceholderText("JSON field holding the token (e.g. JWTToken)")
        self.token_headers = QLineEdit()
        self.token_headers.setPlaceholderText(
            "Extra token-request headers, e.g. Authorization: Bearer key",
        )
        self.token_ttl = QLineEdit("3600")
        self.token_ttl.setPlaceholderText("Fallback TTL in seconds when response has no expires_in")

        self.verify_ssl = QCheckBox("Verify SSL")
        self.verify_ssl.setChecked(True)

        layout.addRow("Token URL:*", self.token_url)
        layout.addRow("Client ID:*", self.client_id)
        layout.addRow("Client Secret:", make_secret_row(self.client_secret))
        layout.addRow("Scope:", self.scope)
        layout.addRow("Client Auth:", self.token_auth)
        layout.addRow("Token Field:", self.token_field_name)
        layout.addRow("Token Headers:", self.token_headers)
        layout.addRow("Token TTL (s):", self.token_ttl)
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
        # Parse "Header: Value" pairs separated by commas into a dict.
        headers: dict[str, str] = {}
        raw_headers = self.token_headers.text().strip()
        if raw_headers:
            for pair in raw_headers.split(","):
                name, _, value = pair.strip().partition(":")
                if name.strip() and value.strip():
                    headers[name.strip()] = value.strip()
        try:
            ttl = int(self.token_ttl.text().strip())
        except ValueError:
            ttl = 3600
        return {
            "token_url": self.token_url.text().strip(),
            "client_id": self.client_id.text().strip(),
            "client_secret": self.client_secret.text().strip() or None,
            "scope": self.scope.text().strip() or None,
            "verify_ssl": self.verify_ssl.isChecked(),
            "token_auth": self.token_auth.currentData(),
            "token_field_name": self.token_field_name.text().strip() or "access_token",
            "token_request_headers": headers or None,
            "default_token_ttl_seconds": ttl,
            "access_token": self.access_token.text().strip() or None,
            "refresh_token": self.refresh_token.text().strip() or None,
        }
