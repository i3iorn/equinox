"""Drag-and-drop enabled QTreeWidget for the collections panel."""

from __future__ import annotations

import logging

from PyQt6.QtCore import QMimeData, Qt, pyqtSignal
from PyQt6.QtGui import QDrag, QDragEnterEvent, QDragMoveEvent, QDropEvent
from PyQt6.QtWidgets import QAbstractItemView, QTreeWidget, QTreeWidgetItem, QWidget

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

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)

    # ── Private helpers ───────────────────────────────────────────────

    @staticmethod
    def _node_data(item: QTreeWidgetItem | None) -> dict[str, object]:
        """Return the UserRole dict for *item* (or ``{}`` when absent)."""
        if item is None:
            return {}
        return item.data(0, Qt.ItemDataRole.UserRole) or {}

    # ── Only request items are draggable ──────────────────────────────

    def startDrag(self, supportedActions: Qt.DropAction) -> None:
        data = self._node_data(self.currentItem())
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

    def dragEnterEvent(self, event: QDragEnterEvent | None) -> None:
        if event is None:
            return
        mime = event.mimeData()
        if mime is not None and mime.hasText():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event: QDragMoveEvent | None) -> None:
        if event is None:
            return
        target = self.itemAt(event.position().toPoint())
        tdata = self._node_data(target)
        if not tdata:
            event.ignore()
            return
        ttype = tdata.get("type")
        if ttype not in ("collection", "folder", "request"):
            event.ignore()
            return

        # Don't show a "valid drop" cursor when hovering over the item being
        # dragged — it can't be dropped onto itself.
        if ttype == "request":
            try:
                mime = event.mimeData()
                if mime is not None and int(mime.text()) == tdata.get("id"):
                    event.ignore()
                    return
            except (ValueError, TypeError):
                pass

        event.acceptProposedAction()

    # ── Handle the drop ───────────────────────────────────────────────

    def dropEvent(self, event: QDropEvent | None) -> None:
        if event is None:
            return
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
        if target_item is None:
            event.ignore()
            return
        tdata = self._node_data(target_item)
        if not tdata:
            event.ignore()
            return

        target_type = tdata.get("type")
        folder: str | None

        if target_type == "collection":
            col_id = tdata.get("id")
            folder = None
        elif target_type == "folder":
            col_id = self._col_id_of(target_item)
            raw_path = tdata.get("path")
            folder = str(raw_path) if raw_path is not None else None
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
                logger.debug("dropEvent: reorder request %s → before %s", request_id, target_req_id)
                event.acceptProposedAction()
                self.request_reorder.emit(request_id, int(target_req_id))
                return
        else:
            event.ignore()
            return

        if col_id is None:
            logger.debug("dropEvent: could not resolve collection id; drop ignored")
            event.ignore()
            return

        # Validate col_id satisfies the signal's declared int type before emitting.
        try:
            col_id = int(col_id)
        except (TypeError, ValueError):
            logger.debug("dropEvent: col_id %r is not a valid integer; drop ignored", col_id)
            event.ignore()
            return

        logger.debug(
            "dropEvent: move request %s → collection %s, folder=%r",
            request_id,
            col_id,
            folder,
        )
        event.acceptProposedAction()
        self.request_dropped.emit(request_id, col_id, folder)

    # ── Tree-walk helpers ─────────────────────────────────────────────

    @staticmethod
    def _col_id_of(item: QTreeWidgetItem) -> int | None:
        """Walk the parent chain to find the enclosing collection's ID."""
        cursor: QTreeWidgetItem | None = item
        while cursor is not None:
            d = DragDropTree._node_data(cursor)
            if d.get("type") == "collection":
                col_id = d.get("id")
                return int(col_id) if col_id is not None else None
            cursor = cursor.parent()
        return None

    @staticmethod
    def _folder_of(item: QTreeWidgetItem) -> str | None:
        """Return the folder path of the item's direct parent, or None for root."""
        parent = item.parent()
        if parent is None:
            return None
        pd = DragDropTree._node_data(parent)
        if pd.get("type") == "folder":
            path = pd.get("path")
            return str(path) if path is not None else None
        return None
