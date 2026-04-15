"""Save-request dialog — prompts for name, collection, and optional folder.

Provides a simple form for the user to specify:
- Request name (or defaults to a method + URL preview)
- Target collection (auto-creates a default if none exist)
- Optional folder hierarchy (e.g. "Auth/OAuth")
"""

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
from equinox.gui.request_panel._constants import (
    SAVE_DIALOG_MIN_WIDTH,
    SAVE_DIALOG_URL_PREVIEW_LEN,
)


class SaveRequestDialog(QDialog):
    """Dialog for saving a request to a collection with optional folder nesting."""

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
        self.setMinimumWidth(SAVE_DIALOG_MIN_WIDTH)

        # Compute default name once — used both as placeholder and final fallback
        self._default_name = f"{method} {url[:SAVE_DIALOG_URL_PREVIEW_LEN]}"

        layout = QVBoxLayout(self)

        # ── Request Name ──────────────────────────────────────────────────
        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Name:"))
        self._name_input = QLineEdit()
        self._name_input.setPlaceholderText(self._default_name)
        name_row.addWidget(self._name_input)
        layout.addLayout(name_row)

        # ── Target Collection ─────────────────────────────────────────────
        col_row = QHBoxLayout()
        col_row.addWidget(QLabel("Collection:"))
        self._col_combo = QComboBox()
        self._populate_collections(db)
        col_row.addWidget(self._col_combo)
        layout.addLayout(col_row)

        # ── Optional Folder Hierarchy ─────────────────────────────────────
        folder_row = QHBoxLayout()
        folder_row.addWidget(QLabel("Folder:"))
        self._folder_input = QLineEdit()
        self._folder_input.setPlaceholderText("e.g. Auth/OAuth  (optional)")
        if current_folder:
            self._folder_input.setText(current_folder)
        folder_row.addWidget(self._folder_input)
        layout.addLayout(folder_row)

        # ── Dialog Buttons ────────────────────────────────────────────────
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_save_clicked)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # ── Private helpers ───────────────────────────────────────────────────

    def _populate_collections(self, db: Database) -> None:
        """Load collections into the combo, auto-creating a default if none exist.

        **Side effect**: Creates a collection named "My Requests" if the
        database is empty. This ensures the user always has a valid target.
        """
        mgr = CollectionManager(db)
        collections = mgr.list_collections()
        if not collections:
            # Auto-create a default collection for first-time users
            mgr.create_collection("My Requests", "Default collection")
            collections = mgr.list_collections()
        for col in collections:
            self._col_combo.addItem(col["name"], col["id"])

    def _validate_inputs(self) -> bool:
        """Validate that a collection is selected.

        Returns:
            True if inputs are valid, False otherwise (warning shown to user).
        """
        if self._col_combo.currentData() is None:
            QMessageBox.warning(
                self, "No Collection", "Please select or create a collection first."
            )
            return False
        return True

    def _on_save_clicked(self) -> None:
        """Accept the dialog after validating inputs."""
        if not self._validate_inputs():
            return
        self.accept()

    # ── Public API ────────────────────────────────────────────────────────

    def result_values(self) -> Tuple[str, int, str, Optional[str]]:
        """Extract and return the user's choices.

        Returns:
            ``(name, collection_id, collection_name, folder_or_none)`` where:
            - ``name`` is the entered name, or the auto-generated default
            - ``collection_id`` is the target collection's DB ID
            - ``collection_name`` is the target collection's human-readable name
            - ``folder_or_none`` is the optional folder path (None if empty)
        """
        name = self._name_input.text().strip() or self._default_name
        col_id: int = self._col_combo.currentData()
        col_name: str = self._col_combo.currentText()
        folder: Optional[str] = self._folder_input.text().strip() or None
        return name, col_id, col_name, folder

