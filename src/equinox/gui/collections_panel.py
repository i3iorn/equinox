"""Collections management panel"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTreeWidget, QTreeWidgetItem,
    QPushButton, QInputDialog, QMessageBox, QMenu, QCheckBox, QLineEdit,
    QDialog, QFormLayout, QComboBox, QDialogButtonBox, QAbstractItemView,
)
from PyQt6.QtCore import pyqtSignal, Qt, QTimer, QMimeData
from PyQt6.QtGui import QAction, QFont, QColor, QShortcut, QKeySequence, QDrag

from equinox.gui.theme import Colors
from equinox.storage import Database, CollectionManager
from equinox.core.request import Request


# ── Lightweight "new request" dialog ─────────────────────────────────────────

class _NewRequestDialog(QDialog):
    """Minimal dialog to create a new request from the collections panel.

    Fields: Name, Method, URL.
    """

    METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]

    def __init__(self, parent=None, title="New Request", folder_hint: str = ""):
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
            hint.setStyleSheet("color: grey;")
            lay.addRow("Folder:", hint)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        lay.addRow(buttons)

        self._name.setFocus()

    def _on_accept(self):
        if not self._url.text().strip():
            QMessageBox.warning(self, "Missing URL", "URL is required.")
            return
        self.accept()

    def values(self):
        """Return (name, method, url) after the dialog is accepted."""
        name = self._name.text().strip() or f"{self._method.currentText()} Request"
        return name, self._method.currentText(), self._url.text().strip()


# ── Panel ─────────────────────────────────────────────────────────────────────


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


class CollectionsPanel(QWidget):
    """Panel for managing collections and requests."""

    request_selected = pyqtSignal(object)
    request_run      = pyqtSignal(object)   # fire-and-forget replay
    collections_changed = pyqtSignal()

    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self.db = db
        self.auto_refresh_enabled = True
        # Expansion state saved when a filter is first typed.
        # ``None`` means "no filter active — read expansion from the live tree".
        self._pre_filter_expansion: "dict | None" = None
        self._programmatic_expand = False  # guard against programmatic expand/collapse signals
        self._init_ui()
        self._setup_auto_refresh()
        self._setup_keyboard_shortcuts()
        self.refresh()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        toolbar = QHBoxLayout()
        self.new_collection_btn = QPushButton("New Collection")
        self.new_collection_btn.clicked.connect(self.create_collection)
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self.refresh)

        self.auto_refresh_checkbox = QCheckBox("Auto-refresh")
        self.auto_refresh_checkbox.setChecked(self.auto_refresh_enabled)
        self.auto_refresh_checkbox.stateChanged.connect(self._toggle_auto_refresh)

        toolbar.addWidget(self.new_collection_btn)
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

        self.tree = _DragDropTree()
        self.tree.setHeaderLabel("Collections")
        self.tree.itemDoubleClicked.connect(self._on_item_double_clicked)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._show_context_menu)
        self.tree.request_dropped.connect(self._on_request_dropped)
        self.tree.request_reorder.connect(self._on_request_reorder)
        self.tree.itemExpanded.connect(self._on_item_expanded)
        self.tree.itemCollapsed.connect(self._on_item_collapsed)
        layout.addWidget(self.tree)

    # #2 — Keyboard shortcuts
    def _setup_keyboard_shortcuts(self):
        """Install keyboard shortcuts on the tree widget."""
        # Enter → open selected request
        enter = QShortcut(QKeySequence(Qt.Key.Key_Return), self.tree)
        enter.activated.connect(self._kbd_open)
        # Delete → delete with confirmation
        delete = QShortcut(QKeySequence(Qt.Key.Key_Delete), self.tree)
        delete.activated.connect(self._kbd_delete)
        # F2 → rename
        f2 = QShortcut(QKeySequence(Qt.Key.Key_F2), self.tree)
        f2.activated.connect(self._kbd_rename)
        # Ctrl+D → duplicate
        dup = QShortcut(QKeySequence("Ctrl+D"), self.tree)
        dup.activated.connect(self._kbd_duplicate)

    def _selected_data(self):
        item = self.tree.currentItem()
        if not item:
            return None, None
        return item, item.data(0, Qt.ItemDataRole.UserRole)

    def _kbd_open(self):
        item, data = self._selected_data()
        if data and data["type"] == "request":
            self._load_request(data["id"])

    def _kbd_delete(self):
        item, data = self._selected_data()
        if not data:
            return
        if data["type"] == "request":
            self._delete_request(data["id"])
        elif data["type"] == "collection":
            self._delete_collection(data["id"])
        elif data["type"] == "folder":
            col_id = self._col_id_for_item(item)
            self._delete_folder(col_id, data["path"])

    def _kbd_rename(self):
        item, data = self._selected_data()
        if not data or not item:
            return
        if data["type"] == "request":
            self._rename_request(data["id"], item)
        elif data["type"] == "collection":
            self._rename_collection(data["id"], item)
        elif data["type"] == "folder":
            col_id = self._col_id_for_item(item)
            self._rename_folder(col_id, data["path"], item)

    def _kbd_duplicate(self):
        item, data = self._selected_data()
        if data and data["type"] == "request":
            self._duplicate_request(data["id"])

    # #2 — Type-to-filter
    def _apply_filter(self, text: str):
        """Show/hide tree items based on filter text.

        When a filter is first typed the current expansion state is
        snapshotted.  While filtered, matching branches are force-expanded.
        When the filter is cleared the snapshot is restored so the tree
        returns to the user's pre-filter layout.
        """
        needle = text.strip().lower()

        if needle:
            # Snapshot expansion state on the *first* non-empty filter
            if self._pre_filter_expansion is None:
                self._pre_filter_expansion = self._get_expansion_state()

            self._programmatic_expand = True
            try:
                for i in range(self.tree.topLevelItemCount()):
                    col_item = self.tree.topLevelItem(i)
                    col_visible = self._filter_subtree(col_item, needle)
                    if needle in col_item.text(0).lower():
                        col_visible = True
                    col_item.setHidden(not col_visible)
                    if col_visible:
                        col_item.setExpanded(True)
            finally:
                self._programmatic_expand = False
        else:
            # Filter cleared → un-hide everything and restore expansion
            saved = self._pre_filter_expansion
            self._pre_filter_expansion = None

            self._programmatic_expand = True
            try:
                for i in range(self.tree.topLevelItemCount()):
                    col_item = self.tree.topLevelItem(i)
                    col_item.setHidden(False)
                    self._unhide_subtree(col_item)

                    data = col_item.data(0, Qt.ItemDataRole.UserRole) or {}
                    col_id = data.get("id")
                    if saved is not None and col_id is not None:
                        col_item.setExpanded(col_id in saved["collections"])
                        self._restore_folder_expansion(col_item, col_id, saved["folders"])
            finally:
                self._programmatic_expand = False

    @staticmethod
    def _unhide_subtree(parent: QTreeWidgetItem) -> None:
        """Recursively un-hide all children."""
        for j in range(parent.childCount()):
            child = parent.child(j)
            child.setHidden(False)
            CollectionsPanel._unhide_subtree(child)

    def _filter_subtree(self, parent: QTreeWidgetItem, needle: str) -> bool:
        """Recursively show/hide children. Returns True if any child is visible."""
        any_visible = False
        for j in range(parent.childCount()):
            child = parent.child(j)
            data = child.data(0, Qt.ItemDataRole.UserRole) or {}
            if data.get("type") == "folder":
                # Recurse into folder; show folder if any child matches
                child_visible = self._filter_subtree(child, needle)
                if not needle or needle in child.text(0).lower():
                    child_visible = True
                child.setHidden(not child_visible)
                if child_visible and needle:
                    child.setExpanded(True)
            else:
                child_visible = not needle or needle in child.text(0).lower()
                child.setHidden(not child_visible)
            if child_visible:
                any_visible = True
        return any_visible

    @staticmethod
    def _restore_folder_expansion(
        parent: QTreeWidgetItem,
        col_id: int,
        folder_set: set,
    ) -> None:
        """Recursively restore folder expansion from *folder_set*."""
        for j in range(parent.childCount()):
            child = parent.child(j)
            cdata = child.data(0, Qt.ItemDataRole.UserRole) or {}
            if cdata.get("type") == "folder":
                key = f"{col_id}:{cdata.get('path', '')}"
                child.setExpanded(key in folder_set)
                CollectionsPanel._restore_folder_expansion(child, col_id, folder_set)

    # ── Track manual expand / collapse while filtered ─────────────────

    def _on_item_expanded(self, item: QTreeWidgetItem) -> None:
        """When user expands an item while a filter is active, update the
        saved pre-filter snapshot so the change persists after clearing."""
        if self._programmatic_expand or self._pre_filter_expansion is None:
            return
        self._update_pre_filter_expansion(item, expanded=True)

    def _on_item_collapsed(self, item: QTreeWidgetItem) -> None:
        """When user collapses an item while a filter is active, update the
        saved pre-filter snapshot so the change persists after clearing."""
        if self._programmatic_expand or self._pre_filter_expansion is None:
            return
        self._update_pre_filter_expansion(item, expanded=False)

    def _update_pre_filter_expansion(self, item: QTreeWidgetItem, expanded: bool) -> None:
        data = item.data(0, Qt.ItemDataRole.UserRole) or {}
        saved = self._pre_filter_expansion
        if saved is None:
            return

        if data.get("type") == "collection":
            col_id = data.get("id")
            if col_id is not None:
                if expanded:
                    saved["collections"].add(col_id)
                else:
                    saved["collections"].discard(col_id)

        elif data.get("type") == "folder":
            col_id = self._col_id_for_item(item)
            path = data.get("path", "")
            if col_id is not None:
                key = f"{col_id}:{path}"
                if expanded:
                    saved["folders"].add(key)
                else:
                    saved["folders"].discard(key)

    def _setup_auto_refresh(self):
        self.refresh_timer = QTimer(self)
        # #5 — Lazy fallback (30 s); immediate refresh via signal wiring
        self.refresh_timer.timeout.connect(self._refresh_if_visible)
        self.refresh_timer.start(30_000)

    def _refresh_if_visible(self):
        if self.isVisible():
            self.refresh()

    def _toggle_auto_refresh(self, state):
        self.auto_refresh_enabled = (state == Qt.CheckState.Checked.value)
        if self.auto_refresh_enabled:
            self.refresh_timer.start(5000)
        else:
            self.refresh_timer.stop()

    # ── Preserve expansion across refreshes ───────────────────────────

    def _get_expansion_state(self) -> dict:
        """Return expansion state for collections and folders.

        Returns:
            Dict with keys:
              - ``"collections"``: set of collection IDs that are expanded
              - ``"folders"``: set of strings ``"{col_id}:{folder_path}"`` that are expanded
        """
        state: dict = {"collections": set(), "folders": set()}
        for i in range(self.tree.topLevelItemCount()):
            col_item = self.tree.topLevelItem(i)
            data = col_item.data(0, Qt.ItemDataRole.UserRole) or {}
            col_id = data.get("id")
            if col_item.isExpanded() and col_id is not None:
                state["collections"].add(col_id)
            # Walk children to find expanded folder nodes
            self._collect_folder_expansion(col_item, col_id, state["folders"])
        return state

    def _collect_folder_expansion(
        self,
        parent: QTreeWidgetItem,
        col_id,
        folder_set: set,
    ) -> None:
        """Recursively collect expanded folder keys into *folder_set*."""
        for j in range(parent.childCount()):
            child = parent.child(j)
            cdata = child.data(0, Qt.ItemDataRole.UserRole) or {}
            if cdata.get("type") == "folder" and child.isExpanded() and col_id is not None:
                folder_set.add(f"{col_id}:{cdata.get('path', '')}")
            self._collect_folder_expansion(child, col_id, folder_set)

    def refresh(self):
        """Refresh collections tree, preserving expansion state.

        When a filter is active the live tree has items force-expanded, so
        we use the saved ``_pre_filter_expansion`` snapshot instead of
        reading the (distorted) live state.
        """
        if self._pre_filter_expansion is not None:
            # A filter is active — the live tree is force-expanded; use saved state
            exp_state = self._pre_filter_expansion
        else:
            exp_state = self._get_expansion_state()

        self._programmatic_expand = True
        try:
            self.tree.clear()
            mgr = CollectionManager(self.db)
            collections = mgr.list_collections()

            for col in collections:
                col_id = col["id"]
                col_item = QTreeWidgetItem([col["name"]])
                f = col_item.font(0)
                f.setBold(True)
                col_item.setFont(0, f)
                col_item.setData(0, Qt.ItemDataRole.UserRole, {"type": "collection", "id": col_id})
                self.tree.addTopLevelItem(col_item)

                # ── Materialise explicit (possibly-empty) folder records first ──
                folder_items: dict[str, QTreeWidgetItem] = {}
                for folder_path in mgr.list_folders(col_id):
                    self._ensure_folder_item(
                        col_item, folder_path, folder_items,
                        exp_state["folders"], col_id,
                    )

                # ── Group requests by folder, counting per node (#11) ─────────
                folder_counts: dict[str, int] = {}
                col_root_count = 0

                for req in mgr.list_requests(col_id):
                    method = req["method"]
                    color = Colors.METHOD.get(method, Colors.MUTED)
                    folder_path = (req.get("folder") or "").strip()

                    # Determine parent node: a folder sub-item or the collection itself
                    if folder_path:
                        parent = self._ensure_folder_item(
                            col_item, folder_path, folder_items,
                            exp_state["folders"], col_id,
                        )
                        folder_counts[folder_path] = folder_counts.get(folder_path, 0) + 1
                    else:
                        parent = col_item
                        col_root_count += 1

                    display_name = req["name"]
                    # Strip leading folder prefix from the display name if it
                    # was baked in by the Postman importer ("Folder/Name" → "Name")
                    if folder_path and display_name.startswith(folder_path + "/"):
                        display_name = display_name[len(folder_path) + 1:]

                    req_item = QTreeWidgetItem([f"{method}  {display_name}"])
                    req_item.setForeground(0, QColor(color))
                    req_item.setData(0, Qt.ItemDataRole.UserRole, {"type": "request", "id": req["id"]})
                    parent.addChild(req_item)

                # ── Apply count badges (#11) ───────────────────────────────────
                total_reqs = col_root_count + sum(folder_counts.values())
                if total_reqs:
                    col_item.setText(0, f"{col['name']}  ({total_reqs})")
                for fp, fitem in folder_items.items():
                    count = folder_counts.get(fp, 0)
                    leaf = fp.split("/")[-1]
                    fitem.setText(0, f"📁 {leaf}  ({count})" if count else f"📁 {leaf}")

                # Restore expansion — default to **collapsed** for new trees
                should_expand = col_id in exp_state["collections"]
                col_item.setExpanded(should_expand)
        finally:
            self._programmatic_expand = False

        # Re-apply the active filter so it isn't lost on refresh
        self._apply_filter(self._filter_input.text())

    # ── Folder-node helpers ────────────────────────────────────────────

    @staticmethod
    def _ensure_folder_item(
        col_item: QTreeWidgetItem,
        folder_path: str,
        cache: dict,
        expansion_state: "set | None" = None,
        col_id: "int | None" = None,
    ) -> QTreeWidgetItem:
        """Return (creating if needed) a folder QTreeWidgetItem for *folder_path*.

        Supports nested paths like ``"Auth/OAuth"`` by creating intermediate
        nodes as necessary.

        Args:
            col_item: The collection's top-level tree item.
            folder_path: The folder path to create/find (e.g. "Auth/OAuth").
            cache: In-progress mapping of path → QTreeWidgetItem for this refresh.
            expansion_state: Set of ``"{col_id}:{path}"`` keys for previously expanded folders.
            col_id: Collection ID (used to build expansion-state keys).
        """
        if folder_path in cache:
            return cache[folder_path]

        parts = folder_path.split("/")
        current_parent = col_item
        accumulated = ""
        for part in parts:
            accumulated = f"{accumulated}/{part}" if accumulated else part
            if accumulated not in cache:
                folder_item = QTreeWidgetItem([f"📁 {part}"])
                folder_font = folder_item.font(0)
                folder_font.setItalic(True)
                folder_item.setFont(0, folder_font)
                folder_item.setData(
                    0, Qt.ItemDataRole.UserRole,
                    {"type": "folder", "path": accumulated},
                )
                current_parent.addChild(folder_item)
                # Restore expansion state; default collapsed
                should_expand = (
                    expansion_state is not None
                    and col_id is not None
                    and f"{col_id}:{accumulated}" in expansion_state
                )
                folder_item.setExpanded(should_expand)
                cache[accumulated] = folder_item
            current_parent = cache[accumulated]

        return current_parent

    def create_collection(self):
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
        mgr = CollectionManager(self.db)
        return mgr.list_collections()

    def _on_item_double_clicked(self, item: QTreeWidgetItem, column: int):
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return
        if data["type"] == "request":
            mgr = CollectionManager(self.db)
            request = mgr.get_request(data["id"])
            if request:
                self.request_selected.emit(request)
        elif data["type"] == "collection":
            # Inline rename on double-click (#12)
            self._rename_collection(data["id"], item)
        elif data["type"] == "folder":
            # Inline rename on double-click (#12)
            col_id = self._col_id_for_item(item)
            if col_id is not None:
                self._rename_folder(col_id, data["path"], item)

    # ── Context menu (#6: Rename, Duplicate, Run) ─────────────────────

    def _show_context_menu(self, position):
        item = self.tree.itemAt(position)
        if not item:
            return
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return

        menu = QMenu()

        if data["type"] == "collection":
            col_id = data["id"]

            new_req_action = QAction("New Request…", self)
            new_req_action.triggered.connect(lambda: self._new_request_in_collection(col_id))
            menu.addAction(new_req_action)

            add_folder_action = QAction("Add Folder…", self)
            add_folder_action.triggered.connect(lambda: self._create_folder_in_collection(col_id))
            menu.addAction(add_folder_action)

            menu.addSeparator()

            rename_action = QAction("Rename…", self)
            rename_action.triggered.connect(lambda: self._rename_collection(col_id, item))
            menu.addAction(rename_action)

            variables_action = QAction("Manage Variables…", self)
            variables_action.triggered.connect(lambda: self._manage_variables(col_id))
            menu.addAction(variables_action)

            set_auth_action = QAction("Set Auth…", self)
            set_auth_action.triggered.connect(lambda: self._set_collection_auth(col_id))
            menu.addAction(set_auth_action)

            clear_auth_action = QAction("Clear Auth", self)
            clear_auth_action.triggered.connect(lambda: self._clear_collection_auth(col_id))
            menu.addAction(clear_auth_action)

            menu.addSeparator()

            sort_menu = menu.addMenu("Sort Requests")
            sort_az = QAction("Sort A → Z", self)
            sort_az.triggered.connect(lambda: self._sort_group(col_id, None, "alpha"))
            sort_menu.addAction(sort_az)
            sort_method = QAction("Sort by Method", self)
            sort_method.triggered.connect(lambda: self._sort_group(col_id, None, "method"))
            sort_menu.addAction(sort_method)

            menu.addSeparator()

            delete_action = QAction("Delete Collection", self)
            delete_action.triggered.connect(lambda: self._delete_collection(col_id))
            menu.addAction(delete_action)

        elif data["type"] == "folder":
            col_id = self._col_id_for_item(item)
            folder_path = data["path"]

            new_req_action = QAction("New Request Here…", self)
            new_req_action.triggered.connect(
                lambda: self._new_request_in_folder(col_id, folder_path)
            )
            menu.addAction(new_req_action)

            subfolder_action = QAction("Add Subfolder…", self)
            subfolder_action.triggered.connect(
                lambda: self._create_subfolder(col_id, folder_path)
            )
            menu.addAction(subfolder_action)

            menu.addSeparator()

            sort_menu = menu.addMenu("Sort Requests")
            sort_az = QAction("Sort A → Z", self)
            sort_az.triggered.connect(lambda c=col_id, f=folder_path: self._sort_group(c, f, "alpha"))
            sort_menu.addAction(sort_az)
            sort_method = QAction("Sort by Method", self)
            sort_method.triggered.connect(lambda c=col_id, f=folder_path: self._sort_group(c, f, "method"))
            sort_menu.addAction(sort_method)

            set_auth_action = QAction("Set Auth…", self)
            set_auth_action.triggered.connect(
                lambda c=col_id, f=folder_path: self._set_folder_auth(c, f)
            )
            menu.addAction(set_auth_action)

            clear_auth_action = QAction("Clear Auth", self)
            clear_auth_action.triggered.connect(
                lambda c=col_id, f=folder_path: self._clear_folder_auth(c, f)
            )
            menu.addAction(clear_auth_action)

            menu.addSeparator()

            rename_folder_action = QAction("Rename Folder…", self)
            rename_folder_action.triggered.connect(
                lambda: self._rename_folder(col_id, folder_path, item)
            )
            menu.addAction(rename_folder_action)

            menu.addSeparator()

            delete_folder_action = QAction("Delete Folder…", self)
            delete_folder_action.triggered.connect(
                lambda: self._delete_folder(col_id, folder_path)
            )
            menu.addAction(delete_folder_action)

        elif data["type"] == "request":
            open_action = QAction("Open in Editor", self)
            open_action.triggered.connect(lambda: self._load_request(data["id"]))
            menu.addAction(open_action)

            run_action = QAction("▶  Run Now", self)
            run_action.triggered.connect(lambda: self._run_request(data["id"]))
            menu.addAction(run_action)

            menu.addSeparator()

            rename_action = QAction("Rename…", self)
            rename_action.triggered.connect(lambda: self._rename_request(data["id"], item))
            menu.addAction(rename_action)

            duplicate_action = QAction("Duplicate", self)
            duplicate_action.triggered.connect(lambda: self._duplicate_request(data["id"]))
            menu.addAction(duplicate_action)

            move_action = QAction("Move to Folder…", self)
            move_action.triggered.connect(lambda: self._move_to_folder(data["id"]))
            menu.addAction(move_action)

            menu.addSeparator()

            delete_action = QAction("Delete Request", self)
            delete_action.triggered.connect(lambda: self._delete_request(data["id"]))
            menu.addAction(delete_action)

        menu.exec(self.tree.viewport().mapToGlobal(position))

    def _load_request(self, request_id: int):
        mgr = CollectionManager(self.db)
        request = mgr.get_request(request_id)
        if request:
            self.request_selected.emit(request)

    def _run_request(self, request_id: int):
        """Load and immediately fire the request without opening the editor."""
        mgr = CollectionManager(self.db)
        request = mgr.get_request(request_id)
        if request:
            self.request_run.emit(request)

    def _rename_collection(self, collection_id: int, item: QTreeWidgetItem):
        old_name = item.text(0)
        new_name, ok = QInputDialog.getText(
            self, "Rename Collection", "New name:", text=old_name
        )
        if not ok or not new_name.strip() or new_name.strip() == old_name:
            return
        mgr = CollectionManager(self.db)
        try:
            mgr.rename_collection(collection_id, new_name.strip())
            self.refresh()
            self.collections_changed.emit()
        except Exception as exc:
            QMessageBox.critical(self, "Error", str(exc))

    def _rename_request(self, request_id: int, item: QTreeWidgetItem):
        # Strip method prefix from displayed name
        old_display = item.text(0)
        # "GET  My Request" → "My Request"
        parts = old_display.split("  ", 1)
        old_name = parts[1] if len(parts) > 1 else old_display
        new_name, ok = QInputDialog.getText(
            self, "Rename Request", "New name:", text=old_name
        )
        if not ok or not new_name.strip() or new_name.strip() == old_name:
            return
        mgr = CollectionManager(self.db)
        try:
            mgr.rename_request(request_id, new_name.strip())
            self.refresh()
            self.collections_changed.emit()
        except Exception as exc:
            QMessageBox.critical(self, "Error", str(exc))

    def _duplicate_request(self, request_id: int):
        mgr = CollectionManager(self.db)
        try:
            mgr.duplicate_request(request_id)
            self.refresh()
            self.collections_changed.emit()
        except Exception as exc:
            QMessageBox.critical(self, "Error", str(exc))

    # ── Delete ────────────────────────────────────────────────────────

    def _delete_collection(self, collection_id: int):
        reply = QMessageBox.question(
            self, "Confirm Delete",
            "Delete this collection and all its requests?",
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
        reply = QMessageBox.question(
            self, "Confirm Delete", "Delete this request?",
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

    def _manage_variables(self, collection_id: int):
        from equinox.gui.collection_variables_dialog import CollectionVariablesDialog
        mgr = CollectionManager(self.db)
        collection = mgr.get_collection(collection_id)
        if not collection:
            QMessageBox.critical(self, "Error", "Collection not found")
            return

        dialog = CollectionVariablesDialog(self.db, collection_id, collection["name"], self)
        dialog.exec()

    # ── Folder helpers ────────────────────────────────────────────────

    @staticmethod
    def _col_id_for_item(item: QTreeWidgetItem) -> "int | None":
        """Walk the parent chain to find the enclosing collection's ID."""
        cursor = item
        while cursor is not None:
            data = cursor.data(0, Qt.ItemDataRole.UserRole) or {}
            if data.get("type") == "collection":
                return data.get("id")
            cursor = cursor.parent()
        return None

    # ── Folder creation ───────────────────────────────────────────────

    def _create_folder_in_collection(self, col_id: "int | None") -> None:
        """Prompt the user for a folder name and create it under the collection root."""
        if col_id is None:
            return
        path, ok = QInputDialog.getText(
            self, "Add Folder", "Folder name or path (e.g. Auth or Auth/OAuth):"
        )
        if not ok or not path.strip():
            return
        mgr = CollectionManager(self.db)
        try:
            mgr.create_folder(col_id, path.strip())
            self.refresh()
            self.collections_changed.emit()
        except Exception as exc:
            QMessageBox.critical(self, "Error", str(exc))

    def _create_subfolder(self, col_id: "int | None", parent_path: str) -> None:
        """Prompt the user for a subfolder name and create it under *parent_path*."""
        if col_id is None:
            return
        name, ok = QInputDialog.getText(
            self, "Add Subfolder", f"Subfolder name (inside \"{parent_path}\"):"
        )
        if not ok or not name.strip():
            return
        full_path = f"{parent_path}/{name.strip()}"
        mgr = CollectionManager(self.db)
        try:
            mgr.create_folder(col_id, full_path)
            self.refresh()
            self.collections_changed.emit()
        except Exception as exc:
            QMessageBox.critical(self, "Error", str(exc))

    # ── Request creation from the panel ──────────────────────────────

    def _new_request_in_collection(self, col_id: "int | None") -> None:
        """Create a new request at the collection root and open it in the editor."""
        if col_id is None:
            return
        dlg = _NewRequestDialog(self, title="New Request")
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        name, method, url = dlg.values()
        self._save_and_open_request(col_id, name, method, url, folder=None)

    def _new_request_in_folder(self, col_id: "int | None", folder_path: str) -> None:
        """Create a new request inside *folder_path* and open it in the editor."""
        if col_id is None:
            return
        dlg = _NewRequestDialog(
            self, title=f"New Request in \"{folder_path}\"", folder_hint=folder_path
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        name, method, url = dlg.values()
        self._save_and_open_request(col_id, name, method, url, folder=folder_path)

    def _save_and_open_request(
        self,
        col_id: int,
        name: str,
        method: str,
        url: str,
        folder: "str | None",
    ) -> None:
        """Persist a new request and emit request_selected to open it in the editor."""
        mgr = CollectionManager(self.db)
        req = Request(
            method=method,
            url=url,
            name=name,
            collection_id=col_id,
            folder=folder,
        )
        try:
            req_id = mgr.save_request(req, collection_id=col_id, name=name)
            saved = mgr.get_request(req_id)
            if saved:
                self.request_selected.emit(saved)
            self.refresh()
            self.collections_changed.emit()
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to create request: {exc}")

    # ── Folder rename / delete ────────────────────────────────────────

    def _rename_folder(
        self,
        col_id: "int | None",
        old_path: str,
        item: QTreeWidgetItem,
    ) -> None:
        if col_id is None:
            return
        new_path, ok = QInputDialog.getText(
            self, "Rename Folder", "New folder name/path:", text=old_path
        )
        if not ok or not new_path.strip() or new_path.strip() == old_path:
            return
        mgr = CollectionManager(self.db)
        try:
            mgr.rename_folder(col_id, old_path, new_path.strip())
            self.refresh()
            self.collections_changed.emit()
        except Exception as exc:
            QMessageBox.critical(self, "Error", str(exc))

    def _delete_folder(self, col_id: "int | None", folder_path: str) -> None:
        if col_id is None:
            return
        reply = QMessageBox.question(
            self, "Delete Folder",
            f"Delete folder \"{folder_path}\"?\n\n"
            "Choose Yes to move requests to root, or No to delete them.",
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No
            | QMessageBox.StandardButton.Cancel,
        )
        if reply == QMessageBox.StandardButton.Cancel:
            return
        move_to_root = reply == QMessageBox.StandardButton.Yes
        mgr = CollectionManager(self.db)
        try:
            mgr.delete_folder(col_id, folder_path, move_to_root=move_to_root)
            self.refresh()
            self.collections_changed.emit()
        except Exception as exc:
            QMessageBox.critical(self, "Error", str(exc))

    def _move_to_folder(self, request_id: int) -> None:
        folder_path, ok = QInputDialog.getText(
            self, "Move to Folder",
            "Folder path (leave empty to move to root):",
        )
        if not ok:
            return
        mgr = CollectionManager(self.db)
        try:
            mgr.move_request_to_folder(request_id, folder_path.strip() or None)
            self.refresh()
            self.collections_changed.emit()
        except Exception as exc:
            QMessageBox.critical(self, "Error", str(exc))

    def _on_request_dropped(self, request_id: int, target_col_id: int, target_folder) -> None:
        """Handle a drag-and-drop move of a request to a new collection/folder."""
        mgr = CollectionManager(self.db)
        try:
            # Check if it's a cross-collection or same-collection move
            req = mgr.get_request(request_id)
            if not req:
                return

            source_col = req.collection_id
            source_folder = req.folder

            # Nothing to do if destination is identical
            if source_col == target_col_id and (source_folder or None) == (target_folder or None):
                return

            if source_col != target_col_id:
                mgr.move_request_to_collection(request_id, target_col_id, target_folder)
            else:
                mgr.move_request_to_folder(request_id, target_folder)

            self.refresh()
            self.collections_changed.emit()
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to move request: {exc}")

    def _on_request_reorder(self, dragged_id: int, target_id: int) -> None:
        """Handle reordering: place *dragged_id* immediately before *target_id*."""
        mgr = CollectionManager(self.db)
        try:
            target_row = mgr.db.fetchone(
                "SELECT collection_id, folder FROM requests WHERE id=?", (target_id,),
            )
            dragged_row = mgr.db.fetchone(
                "SELECT collection_id, folder FROM requests WHERE id=?", (dragged_id,),
            )
            if not target_row or not dragged_row:
                return

            t_col = target_row["collection_id"]
            t_folder = target_row["folder"] or None
            d_col = dragged_row["collection_id"]
            d_folder = dragged_row["folder"] or None

            # If the dragged item is in a different group, move it first
            if d_col != t_col or d_folder != t_folder:
                if d_col != t_col:
                    mgr.move_request_to_collection(dragged_id, t_col, t_folder)
                else:
                    mgr.move_request_to_folder(dragged_id, t_folder)

            # Build ordered list for this group
            group = mgr._select_group(t_col, t_folder)
            ordered_ids = [r["id"] for r in group if r["id"] != dragged_id]
            # Insert dragged before target
            try:
                insert_at = ordered_ids.index(target_id)
            except ValueError:
                insert_at = len(ordered_ids)
            ordered_ids.insert(insert_at, dragged_id)
            mgr.reorder_requests(ordered_ids)

            self.refresh()
            self.collections_changed.emit()
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to reorder: {exc}")

    def _sort_group(self, col_id: int, folder: "str | None", mode: str) -> None:
        """Sort requests in a collection/folder group."""
        mgr = CollectionManager(self.db)
        try:
            if mode == "alpha":
                mgr.sort_requests_alphabetically(col_id, folder)
            elif mode == "method":
                mgr.sort_requests_by_method(col_id, folder)
            self.refresh()
            self.collections_changed.emit()
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to sort: {exc}")

    # ── Hierarchical auth ─────────────────────────────────────────────

    def _set_collection_auth(self, col_id: int) -> None:
        """Open the auth dialog and persist the result on the collection."""
        mgr = CollectionManager(self.db)
        current_auth = mgr.get_collection_auth(col_id)
        from equinox.gui.auth_dialog import AuthDialog
        dialog = AuthDialog(current_auth, self, db=self.db)
        if dialog.exec() == QDialog.DialogCode.Accepted and hasattr(dialog, "_saved_auth"):
            mgr.set_collection_auth(col_id, dialog._saved_auth)
            self.collections_changed.emit()

    def _clear_collection_auth(self, col_id: int) -> None:
        mgr = CollectionManager(self.db)
        mgr.set_collection_auth(col_id, None)
        self.collections_changed.emit()

    def _set_folder_auth(self, col_id: int, folder_path: str) -> None:
        """Open the auth dialog and persist the result on the folder."""
        mgr = CollectionManager(self.db)
        current_auth = mgr.get_folder_auth(col_id, folder_path)
        from equinox.gui.auth_dialog import AuthDialog
        dialog = AuthDialog(current_auth, self, db=self.db)
        if dialog.exec() == QDialog.DialogCode.Accepted and hasattr(dialog, "_saved_auth"):
            mgr.set_folder_auth(col_id, folder_path, dialog._saved_auth)
            self.collections_changed.emit()

    def _clear_folder_auth(self, col_id: int, folder_path: str) -> None:
        mgr = CollectionManager(self.db)
        mgr.set_folder_auth(col_id, folder_path, None)
        self.collections_changed.emit()

