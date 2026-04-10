"""Drag-and-drop enabled QTreeWidget for the collections panel."""

import logging
from typing import Optional

from PyQt6.QtWidgets import QTreeWidget, QTreeWidgetItem, QAbstractItemView
from PyQt6.QtCore import pyqtSignal, Qt, QMimeData
from PyQt6.QtGui import QDrag, QDragEnterEvent, QDragMoveEvent, QDropEvent

logger = logging.getLogger(__name__)


class DragDropTree(QTreeWidget):
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

    def _item_data(self, item: Optional[QTreeWidgetItem]) -> Optional[dict]:
        return item.data(0, Qt.ItemDataRole.UserRole) if item else None

    def startDrag(self, supportedActions: Qt.DropAction) -> None:
        item = self.currentItem()
        data = self._item_data(item)
        if not data or data.get("type") != "request":
            return  # only requests are draggable

        request_id = data.get("id")
        if request_id is None:
            logger.debug("startDrag: request item has no 'id'; drag aborted")
            return

        drag = QDrag(self)
        mime = QMimeData()
        mime.setText(str(request_id))
        drag.setMimeData(mime)
        result = drag.exec(Qt.DropAction.MoveAction)
        logger.debug("startDrag: drag finished (action=%s, request_id=%s)", result, request_id)

    # ── Visual feedback: highlight valid targets ──────────────────────

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasText():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        target = self.itemAt(event.position().toPoint())
        tdata = self._item_data(target)
        if not tdata or tdata.get("type") not in ("collection", "folder", "request"):
            event.ignore()
            return

        # Don't show a "valid drop" cursor when hovering over the item being
        # dragged — it can't be dropped onto itself.
        if tdata.get("type") == "request":
            try:
                if int(event.mimeData().text()) == tdata.get("id"):
                    event.ignore()
                    return
            except (ValueError, TypeError):
                pass

        event.acceptProposedAction()

    # ── Handle the drop ───────────────────────────────────────────────

    def dropEvent(self, event: QDropEvent) -> None:
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
            col_id = tdata.get("id")  # .get() avoids KeyError on malformed data
            folder = None
        elif target_type == "folder":
            col_id = self._col_id_of(target_item)
            folder = tdata.get("path")
        elif target_type == "request":
            target_req_id = tdata.get("id")
            col_id = self._col_id_of(target_item)
            folder = self._folder_of(target_item)
            # Silently ignore self-drops — the item is already where it is.
            if target_req_id is not None and target_req_id == request_id:
                logger.debug("dropEvent: self-drop ignored (request_id=%s)", request_id)
                event.ignore()
                return
            if target_req_id is not None:
                logger.debug(
                    "dropEvent: reorder request %s → before %s", request_id, target_req_id
                )
                event.acceptProposedAction()
                self.request_reorder.emit(request_id, target_req_id)
                return
        else:
            event.ignore()
            return

        if col_id is None:
            logger.debug("dropEvent: could not resolve collection id; drop ignored")
            event.ignore()
            return

        logger.debug(
            "dropEvent: move request %s → collection %s, folder=%r",
            request_id, col_id, folder,
        )
        event.acceptProposedAction()
        self.request_dropped.emit(request_id, col_id, folder)

    # ── Helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _col_id_of(item: QTreeWidgetItem) -> Optional[int]:
        """Walk parent chain to find the enclosing collection ID."""
        cursor = item
        while cursor is not None:
            d = cursor.data(0, Qt.ItemDataRole.UserRole) or {}
            if d.get("type") == "collection":
                return d.get("id")
            cursor = cursor.parent()
        return None

    @staticmethod
    def _folder_of(item: QTreeWidgetItem) -> Optional[str]:
        """Return the folder path of the item's direct parent (or None for root)."""
        parent = item.parent()
        if parent is None:
            return None
        pd = parent.data(0, Qt.ItemDataRole.UserRole) or {}
        if pd.get("type") == "folder":
            return pd.get("path")
        return None


# Backward-compat alias
_DragDropTree = DragDropTree

