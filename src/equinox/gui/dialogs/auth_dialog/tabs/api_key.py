from equinox.gui.dialogs.auth_dialog.tabs.base import AuthDialogTab
from equinox.gui.dialogs.auth_dialog.tabs.base import config_text
from equinox.gui.widgets import make_secret_row
from PyQt6.QtWidgets import QComboBox
from PyQt6.QtWidgets import QFormLayout
from PyQt6.QtWidgets import QLineEdit
from PyQt6.QtWidgets import QWidget


class ApiKeyAuthTab(AuthDialogTab, QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QFormLayout(self)
        layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        self.key_name = QLineEdit()
        self.key_name.setPlaceholderText("X-API-Key")
        self.key_value = QLineEdit()
        self.key_value.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_value.setPlaceholderText("your-api-key")
        self.location = QComboBox()
        self.location.addItems(["header", "query"])

        layout.addRow("Key Name:*", self.key_name)
        layout.addRow("Key Value:*", make_secret_row(self.key_value))
        layout.addRow("Add To:", self.location)

    def get_auth_config(self) -> dict[str, str]:
        return {
            "key": self.key_name.text().strip(),
            "value": self.key_value.text().strip(),
            "location": self.location.currentText(),
        }

    def set_auth_config(self, config: dict[str, str | None]) -> None:
        self.key_name.setText(config_text(config, "key"))
        self.key_value.setText(config_text(config, "value"))
        location_index = self.location.findText(config_text(config, "location", "header"))
        self.location.setCurrentIndex(max(location_index, 0))

    def validate(self) -> str | None:
        if not self.key_name.text().strip():
            return "Key Name is required for API Key."
        if not self.key_value.text().strip():
            return "Key Value is required for API Key."
        return None
