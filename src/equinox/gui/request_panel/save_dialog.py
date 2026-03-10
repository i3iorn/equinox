"""Save-request dialog — prompts for name, collection, and optional folder."""

from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QComboBox,
    QDialogButtonBox,
)

from equinox.storage import Database


class SaveRequestDialog(QDialog):
    """Prompt the user for name, collection, and optional folder when saving."""

    def __init__(self, db: Database, method: str, url: str, current_folder: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Save Request")
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)

        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Name:"))
        self._name_input = QLineEdit()
        self._name_input.setPlaceholderText(f"{method} {url[:50]}")
        name_row.addWidget(self._name_input)
        layout.addLayout(name_row)

        col_row = QHBoxLayout()
        col_row.addWidget(QLabel("Collection:"))
        self._col_combo = QComboBox()
        from equinox.storage import CollectionManager
        mgr = CollectionManager(db)
        collections = mgr.list_collections()
        if not collections:
            mgr.create_collection("My Requests", "Default collection")
            collections = mgr.list_collections()
        for col in collections:
            self._col_combo.addItem(col["name"], col["id"])
        col_row.addWidget(self._col_combo)
        layout.addLayout(col_row)

        folder_row = QHBoxLayout()
        folder_row.addWidget(QLabel("Folder:"))
        self._folder_input = QLineEdit()
        self._folder_input.setPlaceholderText("e.g. Auth/OAuth  (optional)")
        if current_folder:
            self._folder_input.setText(current_folder)
        folder_row.addWidget(self._folder_input)
        layout.addLayout(folder_row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._method = method
        self._url = url

    def result_values(self) -> tuple:
        """Return ``(name, collection_id, col_name, folder_or_none)``."""
        name = self._name_input.text().strip() or f"{self._method} {self._url[:50]}"
        col_id = self._col_combo.currentData()
        col_name = self._col_combo.currentText()
        folder = self._folder_input.text().strip() or None
        return name, col_id, col_name, folder

