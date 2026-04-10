"""Save-request dialog — prompts for name, collection, and optional folder."""

from typing import Optional, Tuple

from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QComboBox,
    QDialogButtonBox,
    QMessageBox,
)

from equinox.storage import Database, CollectionManager

# Maximum characters of the URL shown in the default request name.
_URL_PREVIEW_LEN = 50


class SaveRequestDialog(QDialog):
    """Prompt the user for name, collection, and optional folder when saving."""

    def __init__(
        self,
        db: Database,
        method: str,
        url: str,
        current_folder: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Save Request")
        self.setMinimumWidth(420)

        # Compute the fallback name once so result_values() doesn't need to
        # store method/url separately.
        self._default_name = f"{method} {url[:_URL_PREVIEW_LEN]}"

        layout = QVBoxLayout(self)

        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Name:"))
        self._name_input = QLineEdit()
        self._name_input.setPlaceholderText(self._default_name)
        name_row.addWidget(self._name_input)
        layout.addLayout(name_row)

        col_row = QHBoxLayout()
        col_row.addWidget(QLabel("Collection:"))
        self._col_combo = QComboBox()
        self._populate_collections(db)
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
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # ── Private helpers ───────────────────────────────────────────────────

    def _populate_collections(self, db: Database) -> None:
        """Load collections into the combo, creating a default one if needed."""
        mgr = CollectionManager(db)
        collections = mgr.list_collections()
        if not collections:
            mgr.create_collection("My Requests", "Default collection")
            collections = mgr.list_collections()
        for col in collections:
            self._col_combo.addItem(col["name"], col["id"])

    def _on_accept(self) -> None:
        """Validate before closing — reject if no collection is available."""
        if self._col_combo.currentData() is None:
            QMessageBox.warning(
                self, "No Collection", "Please select or create a collection first."
            )
            return
        self.accept()

    # ── Public API ────────────────────────────────────────────────────────

    def result_values(self) -> Tuple[str, int, str, Optional[str]]:
        """Return ``(name, collection_id, collection_name, folder_or_none)``."""
        name = self._name_input.text().strip() or self._default_name
        col_id: int = self._col_combo.currentData()
        col_name: str = self._col_combo.currentText()
        folder: Optional[str] = self._folder_input.text().strip() or None
        return name, col_id, col_name, folder

