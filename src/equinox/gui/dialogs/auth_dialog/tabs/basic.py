from equinox.gui.dialogs.auth_dialog.tabs.base import AuthDialogTab
from equinox.gui.dialogs.auth_dialog.tabs.base import config_text
from equinox.gui.widgets import make_secret_row
from PyQt6.QtWidgets import QFormLayout
from PyQt6.QtWidgets import QLineEdit
from PyQt6.QtWidgets import QWidget


class BasicAuthTab(AuthDialogTab, QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QFormLayout(self)
        layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        self.username = QLineEdit()
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)

        layout.addRow("Username:*", self.username)
        layout.addRow("Password:*", make_secret_row(self.password))

    def get_auth_config(self) -> dict[str, str]:
        return {
            "username": self.username.text().strip(),
            "password": self.password.text().strip(),
        }

    def set_auth_config(self, config: dict[str, str | None]) -> None:
        self.username.setText(config_text(config, "username"))
        self.password.setText(config_text(config, "password"))

    def validate(self) -> str | None:
        if not self.username.text().strip():
            return "Username is required for Basic Auth."
        if not self.password.text().strip():
            return "Password is required for Basic Auth."
        return None
