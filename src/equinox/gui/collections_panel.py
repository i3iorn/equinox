"""Collections management panel"""

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QTreeWidget,
    QTreeWidgetItem,
    QPushButton,
    QInputDialog,
    QMessageBox,
    QMenu,
    QCheckBox,
)
from PyQt6.QtCore import pyqtSignal, Qt, QTimer
from PyQt6.QtGui import QAction

from equinox.storage import Database, CollectionManager


class CollectionsPanel(QWidget):
    """Panel for managing collections and requests"""

    request_selected = pyqtSignal(object)  # Request object
    collections_changed = pyqtSignal()  # Emitted when collections change

    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self.db = db
        self.auto_refresh_enabled = True
        self._init_ui()
        self._setup_auto_refresh()
        self.refresh()

    def _init_ui(self):
        """Initialize UI"""
        layout = QVBoxLayout(self)

        # Toolbar
        toolbar = QHBoxLayout()
        self.new_collection_btn = QPushButton("New Collection")
        self.new_collection_btn.clicked.connect(self.create_collection)
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self.refresh)

        # Auto-refresh checkbox
        self.auto_refresh_checkbox = QCheckBox("Auto-refresh")
        self.auto_refresh_checkbox.setChecked(self.auto_refresh_enabled)
        self.auto_refresh_checkbox.stateChanged.connect(self._toggle_auto_refresh)

        toolbar.addWidget(self.new_collection_btn)
        toolbar.addWidget(self.refresh_btn)
        toolbar.addWidget(self.auto_refresh_checkbox)
        toolbar.addStretch()
        layout.addLayout(toolbar)

        # Tree widget
        self.tree = QTreeWidget()
        self.tree.setHeaderLabel("Collections")
        self.tree.itemDoubleClicked.connect(self._on_item_double_clicked)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._show_context_menu)
        layout.addWidget(self.tree)

    def _setup_auto_refresh(self):
        """Setup auto-refresh timer"""
        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self.refresh)
        self.refresh_timer.start(2000)  # Refresh every 2 seconds

    def _toggle_auto_refresh(self, state):
        """Toggle auto-refresh on/off"""
        self.auto_refresh_enabled = (state == Qt.CheckState.Checked.value)
        if self.auto_refresh_enabled:
            self.refresh_timer.start(2000)
        else:
            self.refresh_timer.stop()

    def refresh(self):
        """Refresh collections tree"""
        self.tree.clear()

        mgr = CollectionManager(self.db)
        collections = mgr.list_collections()

        for col in collections:
            col_item = QTreeWidgetItem([col["name"]])
            col_item.setData(0, Qt.ItemDataRole.UserRole, {"type": "collection", "id": col["id"]})
            self.tree.addTopLevelItem(col_item)

            # Load requests in this collection
            requests = mgr.list_requests(col["id"])
            for req in requests:
                req_item = QTreeWidgetItem([f"{req['method']} {req['name']}"])
                req_item.setData(0, Qt.ItemDataRole.UserRole, {"type": "request", "id": req["id"]})
                col_item.addChild(req_item)

            col_item.setExpanded(True)

    def create_collection(self):
        """Create new collection"""
        name, ok = QInputDialog.getText(self, "New Collection", "Collection name:")
        if ok and name:
            mgr = CollectionManager(self.db)
            try:
                mgr.create_collection(name)
                self.refresh()
                self.collections_changed.emit()
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to create collection: {e}")

    def get_collections(self):
        """Get list of all collections (for selection dialogs)"""
        mgr = CollectionManager(self.db)
        return mgr.list_collections()

    def _on_item_double_clicked(self, item: QTreeWidgetItem, column: int):
        """Handle item double click"""
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if data and data["type"] == "request":
            # Load request
            mgr = CollectionManager(self.db)
            request = mgr.get_request(data["id"])
            if request:
                self.request_selected.emit(request)

    def _show_context_menu(self, position):
        """Show context menu for tree items"""
        item = self.tree.itemAt(position)
        if not item:
            return

        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return

        menu = QMenu()

        if data["type"] == "collection":
            delete_action = QAction("Delete Collection", self)
            delete_action.triggered.connect(lambda: self._delete_collection(data["id"]))
            menu.addAction(delete_action)
        elif data["type"] == "request":
            delete_action = QAction("Delete Request", self)
            delete_action.triggered.connect(lambda: self._delete_request(data["id"]))
            menu.addAction(delete_action)

        menu.exec(self.tree.viewport().mapToGlobal(position))

    def _delete_collection(self, collection_id: int):
        """Delete collection"""
        reply = QMessageBox.question(
            self,
            "Confirm Delete",
            "Are you sure you want to delete this collection and all its requests?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )

        if reply == QMessageBox.StandardButton.Yes:
            mgr = CollectionManager(self.db)
            try:
                mgr.delete_collection(collection_id)
                self.refresh()
                self.collections_changed.emit()
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to delete collection: {e}")

    def _delete_request(self, request_id: int):
        """Delete request"""
        reply = QMessageBox.question(
            self,
            "Confirm Delete",
            "Are you sure you want to delete this request?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )

        if reply == QMessageBox.StandardButton.Yes:
            mgr = CollectionManager(self.db)
            try:
                mgr.delete_request(request_id)
                self.refresh()
                self.collections_changed.emit()
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to delete request: {e}")
