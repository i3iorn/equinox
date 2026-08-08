from equinox.gui.dialogs.auth_dialog.tabs.base import AuthDialogTab
from equinox.gui.widgets import make_secret_row
from PyQt6.QtWidgets import QComboBox
from PyQt6.QtWidgets import QFormLayout
from PyQt6.QtWidgets import QLineEdit
from PyQt6.QtWidgets import QWidget


class ApiKeyAuthTab(AuthDialogTab, QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QFormLayout(self)

        self.key_name = QLineEdit()
        self.key_value = QLineEdit()
        self.key_value.setEchoMode(QLineEdit.EchoMode.Password)
        self.location = QComboBox()
        self.location.addItems(["header", "query"])

        layout.addRow("Key Name:", self.key_name)
        layout.addRow("Key Value:", make_secret_row(self.key_value))
        layout.addRow("Add To:", self.location)

    def get_auth_config(self) -> dict[str, str]:
        return {
            "key": self.key_name.text().strip(),
            "value": self.key_value.text().strip(),
            "location": self.location.currentText(),
        }
