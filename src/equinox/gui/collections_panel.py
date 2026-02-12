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
)
from PyQt6.QtCore import pyqtSignal, Qt
from PyQt6.QtGui import QAction

from equinox.storage import Database, CollectionManager


class CollectionsPanel(QWidget):
    """Panel for managing collections and requests"""

    request_selected = pyqtSignal(object)  # Request object

    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self.db = db
        self._init_ui()
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
        toolbar.addWidget(self.new_collection_btn)
        toolbar.addWidget(self.refresh_btn)
        toolbar.addStretch()
        layout.addLayout(toolbar)

        # Tree widget
        self.tree = QTreeWidget()
        self.tree.setHeaderLabel("Collections")
        self.tree.itemDoubleClicked.connect(self._on_item_double_clicked)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._show_context_menu)
        layout.addWidget(self.tree)

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
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to create collection: {e}")

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
            "Are you sure you want to delete this collection?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )

        if reply == QMessageBox.StandardButton.Yes:
            mgr = CollectionManager(self.db)
            mgr.delete_collection(collection_id)
            self.refresh()

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
            mgr.delete_request(request_id)
            self.refresh()
