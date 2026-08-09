"""Save-request dialog — prompts for name, collection, and optional folder.

UI responsibilities only:
- collect a request name (or fall back to a method + URL preview)
- present caller-provided collection choices
- collect an optional folder path
- perform lightweight UI validation before accepting

Non-UI responsibilities intentionally left to the caller / facade:
- loading collections from storage
- auto-creating a default collection when none exist
- performing the actual save/update persistence
"""

from collections.abc import Iterable
from typing import TypedDict

from equinox.gui.error_presenter import ErrorPresenter
from PyQt6.QtWidgets import QComboBox
from PyQt6.QtWidgets import QDialog
from PyQt6.QtWidgets import QDialogButtonBox
from PyQt6.QtWidgets import QHBoxLayout
from PyQt6.QtWidgets import QLabel
from PyQt6.QtWidgets import QLineEdit
from PyQt6.QtWidgets import QVBoxLayout
from PyQt6.QtWidgets import QWidget


SAVE_DIALOG_MIN_WIDTH = 420
SAVE_DIALOG_URL_PREVIEW_LEN = 50


class SaveDialogCollectionChoice(TypedDict):
    """Plain collection choice consumed by :class:`SaveRequestDialog`."""

    id: int
    name: str


class SaveRequestDialog(QDialog):
    """Pure UI form for choosing request name, collection, and folder."""

    def __init__(
        self,
        collections: Iterable[SaveDialogCollectionChoice],
        method: str,
        url: str,
        current_folder: str = "",
        parent: QWidget | None = None,
    ) -> None:
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
        self._populate_collections(collections)
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
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel,
        )
        buttons.accepted.connect(self._on_save_clicked)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # ── Private helpers ───────────────────────────────────────────────────

    def _populate_collections(self, collections: Iterable[SaveDialogCollectionChoice]) -> None:
        """Load caller-provided collection choices into the combo box."""
        for col in collections:
            self._col_combo.addItem(col["name"], col["id"])

    def _validate_inputs(self) -> bool:
        """Validate UI state only.

        Returns:
            True if the user selected a collection, False otherwise.

        Notes:
            This method intentionally performs only form-level validation. It
            does not reach into storage, create collections, or persist data.
        """
        if self._col_combo.currentData() is None:
            ErrorPresenter.warning(
                self,
                "Please select or create a collection first.",
                title="No Collection",
            )
            return False
        return True

    def _on_save_clicked(self) -> None:
        """Accept the dialog after validating inputs."""
        if not self._validate_inputs():
            return
        self.accept()

    # ── Public API ────────────────────────────────────────────────────────

    def result_values(self) -> tuple[str, int, str, str | None]:
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
        folder: str | None = self._folder_input.text().strip() or None
        return name, col_id, col_name, folder
