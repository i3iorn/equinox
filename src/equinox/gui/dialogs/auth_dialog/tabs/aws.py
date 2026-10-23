from equinox.gui.dialogs.auth_dialog.tabs.base import AuthDialogTab
from equinox.gui.dialogs.auth_dialog.tabs.base import config_text
from equinox.gui.widgets import make_secret_row
from PyQt6.QtWidgets import QFormLayout
from PyQt6.QtWidgets import QLineEdit
from PyQt6.QtWidgets import QWidget


class AwsSigV4AuthTab(AuthDialogTab, QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QFormLayout(self)
        layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        self.access_key = QLineEdit()
        self.access_key.setPlaceholderText("AKIAIOSFODNN7EXAMPLE")  # pragma: allowlist secret
        self.secret_key = QLineEdit()
        self.secret_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.secret_key.setPlaceholderText(
            "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",  # pragma: allowlist secret
        )
        self.region = QLineEdit()
        self.region.setPlaceholderText("us-east-1")
        self.service = QLineEdit()
        self.service.setPlaceholderText("execute-api")
        self.session_token = QLineEdit()
        self.session_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.session_token.setPlaceholderText("Optional — STS session token")

        layout.addRow("Access Key ID:*", self.access_key)
        layout.addRow("Secret Access Key:*", make_secret_row(self.secret_key))
        layout.addRow("Region:*", self.region)
        layout.addRow("Service:*", self.service)
        layout.addRow("Session Token:", make_secret_row(self.session_token))

    def get_auth_config(self) -> dict[str, str | None]:
        return {
            "access_key": self.access_key.text().strip(),
            "secret_key": self.secret_key.text().strip(),
            "region": self.region.text().strip(),
            "service": self.service.text().strip(),
            "session_token": self.session_token.text().strip() or None,
        }

    def set_auth_config(self, config: dict[str, str | None]) -> None:
        self.access_key.setText(config_text(config, "access_key"))
        self.secret_key.setText(config_text(config, "secret_key"))
        self.region.setText(config_text(config, "region"))
        self.service.setText(config_text(config, "service"))
        self.session_token.setText(config_text(config, "session_token"))

    def validate(self) -> str | None:
        if not self.access_key.text().strip():
            return "Access Key ID is required for AWS SigV4."
        if not self.secret_key.text().strip():
            return "Secret Access Key is required for AWS SigV4."
        if not self.region.text().strip():
            return "Region is required for AWS SigV4."
        if not self.service.text().strip():
            return "Service is required for AWS SigV4."
        return None
