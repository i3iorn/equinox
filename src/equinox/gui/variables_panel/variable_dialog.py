"""Variable add/edit dialog."""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QTextEdit,
    QWidget,
)


class VariableDialog(QDialog):
    """Dialog for adding or editing a single variable entry."""

    def __init__(
        self,
        parent: QWidget | None = None,
        key: str = "",
        value: str = "",
        description: str = "",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Variable")
        self.setMinimumWidth(500)

        layout = QFormLayout(self)

        self.key_input = QLineEdit(key)
        self.key_input.setPlaceholderText("e.g., API_URL")
        layout.addRow("Key:", self.key_input)

        self.value_input = QLineEdit(value)
        self.value_input.setPlaceholderText("e.g., https://api.example.com")
        layout.addRow("Value:", self.value_input)

        self.description_input = QTextEdit(description)
        self.description_input.setPlaceholderText("Optional description")
        self.description_input.setMaximumHeight(80)
        layout.addRow("Description:", self.description_input)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def get_values(self) -> tuple[str, str, str]:
        """Return (key, value, description) stripped of surrounding whitespace."""
        return (
            self.key_input.text().strip(),
            self.value_input.text(),
            self.description_input.toPlainText().strip(),
        )
