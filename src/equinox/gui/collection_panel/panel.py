"""Collections management panel"""
import logging
from typing import Any

from equinox.application.collections import CollectionFacade
from equinox.gui.collection_panel._dialog_registry import DialogRegistry
from equinox.gui.collection_panel._spec_export_service import ApiSpecExportService
from equinox.gui.collection_panel.actions import _CollectionsActionsMixin
from equinox.gui.collection_panel.panel_mixins import _CollectionsApiSpecMixin
from equinox.gui.collection_panel.panel_mixins import _CollectionsContextMenuMixin
from equinox.gui.collection_panel.panel_mixins import _CollectionsRefreshTreeMixin
from equinox.gui.collection_panel.panel_mixins import _CollectionsSelectionFilterMixin
from equinox.gui.error_presenter import ErrorPresenter
from equinox.gui.widgets.drag_drop_tree import DragDropTree
from equinox.storage import Database
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QCheckBox
from PyQt6.QtWidgets import QComboBox
from PyQt6.QtWidgets import QDialog
from PyQt6.QtWidgets import QDialogButtonBox
from PyQt6.QtWidgets import QFormLayout
from PyQt6.QtWidgets import QHBoxLayout
from PyQt6.QtWidgets import QInputDialog
from PyQt6.QtWidgets import QLineEdit
from PyQt6.QtWidgets import QPushButton
from PyQt6.QtWidgets import QVBoxLayout
from PyQt6.QtWidgets import QWidget

logger = logging.getLogger(__name__)


class _NewRequestDialog(QDialog):
    """Minimal dialog to create a new request from the collections panel.

    Fields: Name, Method, URL.
    """

    METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]

    def __init__(
        self, parent: QWidget | None = None, title: str = "New Request", folder_hint: str = "",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(420)

        lay = QFormLayout(self)
        lay.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        self._name = QLineEdit()
        self._name.setPlaceholderText("Request name")
        self._method = QComboBox()
        self._method.addItems(self.METHODS)
        self._url = QLineEdit()
        self._url.setPlaceholderText("https://")

        lay.addRow("Name:", self._name)
        lay.addRow("Method:", self._method)
        lay.addRow("URL:", self._url)

        if folder_hint:
            hint = QLineEdit(folder_hint)
            hint.setReadOnly(True)
            hint.setObjectName("hint")
            lay.addRow("Folder:", hint)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        lay.addRow(buttons)

        self._name.setFocus()

    def _on_accept(self) -> None:
        if not self._url.text().strip():
            ErrorPresenter.warning(self, "URL is required.", title="Missing URL")
            return
        self.accept()

    def values(self) -> tuple[str, str, str]:
        """Return (name, method, url) after the dialog is accepted."""
        name = self._name.text().strip() or f"{self._method.currentText()} Request"
        return name, self._method.currentText(), self._url.text().strip()


# ── Panel ─────────────────────────────────────────────────────────────────────


class CollectionsPanel(
    _CollectionsActionsMixin,  # type: ignore[misc]
    _CollectionsSelectionFilterMixin,
    _CollectionsRefreshTreeMixin,
    _CollectionsContextMenuMixin,
    _CollectionsApiSpecMixin,
    QWidget,
):
    """Panel for managing collections and requests."""

    request_selected = pyqtSignal(object)
    request_run = pyqtSignal(object)  # fire-and-forget replay
    collections_changed = pyqtSignal()

    def __init__(
        self,
        db: Database,
        parent: QWidget | None = None,
        collection_facade: "CollectionFacade | None" = None,
    ) -> None:
        super().__init__(parent)
        self.db = db
        self._collection_facade = collection_facade or CollectionFacade(db)
        self._api_spec_service = ApiSpecExportService(db, logger)
        self._dialog_registry = DialogRegistry()
        self.auto_refresh_enabled = True
        self._pre_filter_expansion: dict[str, set[Any]] | None = None
        self._programmatic_expand = False
        self._init_ui()
        self._setup_auto_refresh()
        self._setup_keyboard_shortcuts()
        self.refresh()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        toolbar = QHBoxLayout()
        self.new_collection_btn = QPushButton("New Collection")
        self.new_collection_btn.clicked.connect(self.create_collection)
        self.import_btn = QPushButton("Import Openapi/Swagger")
        parent_widget = self.parent()
        if parent_widget is not None and hasattr(parent_widget, "_import_openapi"):
            self.import_btn.clicked.connect(parent_widget._import_openapi)
        else:
            self.import_btn.setEnabled(False)
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self.refresh)

        self.auto_refresh_checkbox = QCheckBox("Auto-refresh")
        self.auto_refresh_checkbox.setChecked(self.auto_refresh_enabled)
        self.auto_refresh_checkbox.stateChanged.connect(self._toggle_auto_refresh)

        toolbar.addWidget(self.new_collection_btn)
        toolbar.addWidget(self.import_btn)
        toolbar.addWidget(self.refresh_btn)
        toolbar.addWidget(self.auto_refresh_checkbox)
        toolbar.addStretch()
        layout.addLayout(toolbar)

        # #2 — Filter box for type-to-search
        self._filter_input = QLineEdit()
        self._filter_input.setPlaceholderText("Filter collections / requests…")
        self._filter_input.setClearButtonEnabled(True)
        self._filter_input.textChanged.connect(self._apply_filter)
        layout.addWidget(self._filter_input)

        self.tree = DragDropTree()
        self.tree.setHeaderLabel("Collections")
        self.tree.itemDoubleClicked.connect(self._on_item_double_clicked)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._show_context_menu)
        self.tree.request_dropped.connect(self._on_request_dropped)
        self.tree.request_reorder.connect(self._on_request_reorder)
        self.tree.itemExpanded.connect(self._on_item_expanded)
        self.tree.itemCollapsed.connect(self._on_item_collapsed)
        layout.addWidget(self.tree)

    def create_collection(self) -> None:
        name, ok = QInputDialog.getText(self, "New Collection", "Collection name:")
        if ok and name:
            try:
                self._collection_facade.create_collection(name)
                self.refresh()
                self.collections_changed.emit()
            except Exception as exc:
                ErrorPresenter.error(self, f"Failed to create collection: {exc}")

    def get_collections(self) -> list[dict[str, Any]]:
        logger.debug("get_collections called")
        return list(self._collection_facade.list_collections())
