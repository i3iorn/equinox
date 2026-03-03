"""Drag-and-drop enabled QTreeWidget for the collections panel."""

from PyQt6.QtWidgets import QTreeWidget, QTreeWidgetItem, QAbstractItemView
from PyQt6.QtCore import pyqtSignal, Qt, QMimeData
from PyQt6.QtGui import QDrag


class _DragDropTree(QTreeWidget):
    """QTreeWidget subclass that supports dragging request items
    onto collections or folders (including cross-collection moves)."""

    # Emitted after a successful drop so the panel can refresh & persist.
    # Args: request_id (int), target_collection_id (int), target_folder (str|None)
    request_dropped = pyqtSignal(int, int, object)
    # Emitted when a request is dropped onto a sibling request (reorder).
    # Args: dragged_request_id (int), target_request_id (int)
    request_reorder = pyqtSignal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)

    # ── Only request items are draggable ──────────────────────────────

    def _item_data(self, item: QTreeWidgetItem):
        return item.data(0, Qt.ItemDataRole.UserRole) if item else None

    def startDrag(self, supportedActions):
        item = self.currentItem()
        data = self._item_data(item)
        if not data or data.get("type") != "request":
            return  # only requests are draggable

        drag = QDrag(self)
        mime = QMimeData()
        mime.setText(str(data["id"]))
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.MoveAction)

    # ── Visual feedback: highlight valid targets ──────────────────────

    def dragEnterEvent(self, event):
        if event.mimeData().hasText():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        target = self.itemAt(event.position().toPoint())
        tdata = self._item_data(target)
        if tdata and tdata.get("type") in ("collection", "folder", "request"):
            event.acceptProposedAction()
        else:
            event.ignore()

    # ── Handle the drop ───────────────────────────────────────────────

    def dropEvent(self, event):
        mime = event.mimeData()
        if not mime or not mime.hasText():
            event.ignore()
            return

        try:
            request_id = int(mime.text())
        except (ValueError, TypeError):
            event.ignore()
            return

        target_item = self.itemAt(event.position().toPoint())
        tdata = self._item_data(target_item)
        if not tdata:
            event.ignore()
            return

        # Resolve target collection + folder
        target_type = tdata.get("type")
        if target_type == "collection":
            col_id = tdata["id"]
            folder = None
        elif target_type == "folder":
            col_id = self._col_id_of(target_item)
            folder = tdata.get("path")
        elif target_type == "request":
            target_req_id = tdata.get("id")
            col_id = self._col_id_of(target_item)
            folder = self._folder_of(target_item)
            # If dropping on a request, emit reorder signal
            if target_req_id is not None and target_req_id != request_id:
                event.acceptProposedAction()
                self.request_reorder.emit(request_id, target_req_id)
                return
        else:
            event.ignore()
            return

        if col_id is None:
            event.ignore()
            return

        event.acceptProposedAction()
        self.request_dropped.emit(request_id, col_id, folder)

    # ── Helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _col_id_of(item: QTreeWidgetItem):
        """Walk parent chain to find the enclosing collection ID."""
        cursor = item
        while cursor is not None:
            d = cursor.data(0, Qt.ItemDataRole.UserRole) or {}
            if d.get("type") == "collection":
                return d.get("id")
            cursor = cursor.parent()
        return None

    @staticmethod
    def _folder_of(item: QTreeWidgetItem):
        """Return the folder path of the item's direct parent (or None for root)."""
        parent = item.parent()
        if parent is None:
            return None
        pd = parent.data(0, Qt.ItemDataRole.UserRole) or {}
        if pd.get("type") == "folder":
            return pd.get("path")
        return None
