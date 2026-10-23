from equinox.gui.dialogs.auth_dialog.tabs.base import AuthDialogTab
from equinox.gui.dialogs.auth_dialog.tabs.base import config_text
from equinox.gui.widgets import make_secret_row
from PyQt6.QtWidgets import QFormLayout
from PyQt6.QtWidgets import QLineEdit
from PyQt6.QtWidgets import QWidget


class BearerAuthTab(AuthDialogTab, QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QFormLayout(self)
        layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        self.token = QLineEdit()
        self.token.setEchoMode(QLineEdit.EchoMode.Password)
        self.token.setPlaceholderText("Paste your bearer token…")

        layout.addRow("Token:*", make_secret_row(self.token))

    def get_auth_config(self) -> dict[str, str]:
        return {"token": self.token.text().strip()}

    def set_auth_config(self, config: dict[str, str | None]) -> None:
        self.token.setText(config_text(config, "token"))

    def validate(self) -> str | None:
        if not self.token.text().strip():
            return "Token is required for Bearer Token."
        return None
