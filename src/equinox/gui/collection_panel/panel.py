"""Collections management panel"""

import logging

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLineEdit,
    QMenu,
    QPushButton,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from equinox.application.collections import CollectionFacade
from equinox.gui.collection_panel._dialog_registry import DialogRegistry
from equinox.gui.collection_panel._spec_export_service import ApiSpecExportService
from equinox.gui.collection_panel.actions import _CollectionsActionsMixin
from equinox.gui.dialogs.api_spec_dialog import ApiSpecDialog
from equinox.gui.error_presenter import ErrorPresenter
from equinox.gui.theme import Colors
from equinox.gui.widgets.drag_drop_tree import DragDropTree
from equinox.storage import Database

logger = logging.getLogger(__name__)

_AUTO_REFRESH_INTERVAL_MS = 30_000


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
            hint.setObjectName("hint")
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
            ErrorPresenter.warning(self, "URL is required.", title="Missing URL")
            return
        self.accept()

    def values(self):
        """Return (name, method, url) after the dialog is accepted."""
        name = self._name.text().strip() or f"{self._method.currentText()} Request"
        return name, self._method.currentText(), self._url.text().strip()


# ── Panel ─────────────────────────────────────────────────────────────────────


class CollectionsPanel(_CollectionsActionsMixin, QWidget):
    """Panel for managing collections and requests."""

    request_selected = pyqtSignal(object)
    request_run = pyqtSignal(object)  # fire-and-forget replay
    collections_changed = pyqtSignal()

    def __init__(
        self,
        db: Database,
        parent=None,
        collection_facade: "CollectionFacade | None" = None,
    ):
        super().__init__(parent)
        self.db = db
        self._collection_facade = collection_facade or CollectionFacade(db)
        self._api_spec_service = ApiSpecExportService(db, logger)
        self._dialog_registry = DialogRegistry()
        self.auto_refresh_enabled = True
        # Expansion state saved when a filter is first typed.
        # ``None`` means "no filter active — read expansion from the live tree".
        self._pre_filter_expansion: dict | None = None
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
        self.refresh_timer.start(_AUTO_REFRESH_INTERVAL_MS)

    def _refresh_if_visible(self):
        if self.isVisible():
            self.refresh()

    def _toggle_auto_refresh(self, state):
        self.auto_refresh_enabled = state == Qt.CheckState.Checked.value
        if self.auto_refresh_enabled:
            self.refresh_timer.start(_AUTO_REFRESH_INTERVAL_MS)
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
            collections = self._collection_facade.list_collections()

            for col in collections:
                col_id = col.get("id")
                # Defensive: ensure col_id is a positive non-boolean integer.
                if isinstance(col_id, bool) or not (isinstance(col_id, int) and col_id > 0):
                    logger.warning(
                        "Skipping collection with invalid id=%r name=%r", col_id, col.get("name")
                    )
                    continue
                col_item = QTreeWidgetItem([col["name"]])
                f = col_item.font(0)
                f.setBold(True)
                col_item.setFont(0, f)
                col_item.setData(0, Qt.ItemDataRole.UserRole, {"type": "collection", "id": col_id})
                self.tree.addTopLevelItem(col_item)

                # ── Materialise explicit (possibly-empty) folder records first ──
                folder_items: dict[str, QTreeWidgetItem] = {}
                for folder_path in self._collection_facade.list_folders(col_id):
                    self._ensure_folder_item(
                        col_item,
                        folder_path,
                        folder_items,
                        exp_state["folders"],
                        col_id,
                    )

                # ── Group requests by folder, counting per node (#11) ─────────
                folder_counts: dict[str, int] = {}
                col_root_count = 0

                for req in self._collection_facade.list_requests(col_id):
                    method = req["method"]
                    color = Colors.METHOD.get(method, Colors.MUTED)
                    folder_path = (req.get("folder") or "").strip()

                    # Determine parent node: a folder sub-item or the collection itself
                    if folder_path:
                        parent = self._ensure_folder_item(
                            col_item,
                            folder_path,
                            folder_items,
                            exp_state["folders"],
                            col_id,
                        )
                        folder_counts[folder_path] = folder_counts.get(folder_path, 0) + 1
                    else:
                        parent = col_item
                        col_root_count += 1

                    display_name = req["name"]
                    # Strip leading folder prefix from the display name if it
                    # was baked in by the Postman importer ("Folder/Name" → "Name")
                    if folder_path and display_name.startswith(folder_path + "/"):
                        display_name = display_name[len(folder_path) + 1 :]

                    req_item = QTreeWidgetItem([f"{method}  {display_name}"])
                    req_item.setForeground(0, QColor(color))
                    req_item.setData(
                        0, Qt.ItemDataRole.UserRole, {"type": "request", "id": req["id"]}
                    )
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
                    0,
                    Qt.ItemDataRole.UserRole,
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
            try:
                self._collection_facade.create_collection(name)
                self.refresh()
                self.collections_changed.emit()
            except Exception as e:
                ErrorPresenter.error(self, f"Failed to create collection: {e}")

    def get_collections(self):
        logger.debug("get_collections called")
        return self._collection_facade.list_collections()

    def _on_item_double_clicked(self, item: QTreeWidgetItem, column: int):
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return
        if data["type"] == "request":
            request = self._collection_facade.get_request(data["id"])
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
            col_id = data.get("id")
            if not isinstance(col_id, int):
                logger.debug("CollectionsPanel: invalid collection id for context menu: %r", col_id)
                return
            create_specs = [
                (
                    "new_request",
                    "New Request…",
                    lambda: self._new_request_in_collection(col_id),
                    False,
                ),
                (
                    "add_folder",
                    "Add Folder…",
                    lambda: self._create_folder_in_collection(col_id),
                    False,
                ),
            ]
            self._add_ranked_context_actions(menu, "collections_collection", create_specs)
            menu.addSeparator()

            manage_specs = []
            if isinstance(col_id, int) and col_id > 0:
                manage_specs.append(
                    (
                        "show_api_spec",
                        "Show API Spec…",
                        lambda cid=col_id: self._show_api_spec_for_collection(cid),
                        False,
                    )
                )
            else:
                logger.debug(
                    "CollectionsPanel: skipping Show API Spec action for invalid collection id: %r",
                    col_id,
                )
            manage_specs.extend(
                [
                    ("rename", "Rename…", lambda: self._rename_collection(col_id, item), False),
                    (
                        "manage_variables",
                        "Manage Variables…",
                        lambda: self._manage_variables(col_id),
                        False,
                    ),
                    ("set_auth", "Set Auth…", lambda: self._set_collection_auth(col_id), False),
                    (
                        "clear_auth",
                        "Clear Auth",
                        lambda: self._clear_collection_auth(col_id),
                        False,
                    ),
                ]
            )
            self._add_ranked_context_actions(menu, "collections_collection", manage_specs)
            menu.addSeparator()

            sort_menu = menu.addMenu("Sort Requests")
            if sort_menu is not None:
                sort_specs = [
                    (
                        "sort_alpha",
                        "Sort A → Z",
                        lambda: self._sort_group(col_id, None, "alpha"),
                        False,
                    ),
                    (
                        "sort_method",
                        "Sort by Method",
                        lambda: self._sort_group(col_id, None, "method"),
                        False,
                    ),
                ]
                self._add_ranked_context_actions(
                    sort_menu, "collections_collection_sort", sort_specs
                )
            menu.addSeparator()

            delete_specs = [
                (
                    "delete_collection",
                    "Delete Collection",
                    lambda: self._delete_collection(col_id),
                    True,
                ),
            ]
            self._add_ranked_context_actions(menu, "collections_collection", delete_specs)

        elif data["type"] == "folder":
            col_id = self._col_id_for_item(item)
            folder_path = data["path"]
            if not isinstance(col_id, int):
                logger.debug(
                    "CollectionsPanel: invalid folder collection id for context menu: %r", col_id
                )
                return
            create_specs = [
                (
                    "new_request_here",
                    "New Request Here…",
                    lambda: self._new_request_in_folder(col_id, folder_path),
                    False,
                ),
                (
                    "add_subfolder",
                    "Add Subfolder…",
                    lambda: self._create_subfolder(col_id, folder_path),
                    False,
                ),
            ]
            self._add_ranked_context_actions(menu, "collections_folder", create_specs)
            menu.addSeparator()

            sort_menu = menu.addMenu("Sort Requests")
            if sort_menu is not None:
                sort_specs = [
                    (
                        "sort_alpha",
                        "Sort A → Z",
                        lambda c=col_id, f=folder_path: self._sort_group(c, f, "alpha"),
                        False,
                    ),
                    (
                        "sort_method",
                        "Sort by Method",
                        lambda c=col_id, f=folder_path: self._sort_group(c, f, "method"),
                        False,
                    ),
                ]
                self._add_ranked_context_actions(sort_menu, "collections_folder_sort", sort_specs)

            manage_specs = [
                (
                    "set_auth",
                    "Set Auth…",
                    lambda c=col_id, f=folder_path: self._set_folder_auth(c, f),
                    False,
                ),
                (
                    "clear_auth",
                    "Clear Auth",
                    lambda c=col_id, f=folder_path: self._clear_folder_auth(c, f),
                    False,
                ),
                (
                    "rename_folder",
                    "Rename Folder…",
                    lambda: self._rename_folder(col_id, folder_path, item),
                    False,
                ),
            ]
            self._add_ranked_context_actions(menu, "collections_folder", manage_specs)
            menu.addSeparator()

            delete_specs = [
                (
                    "delete_folder",
                    "Delete Folder…",
                    lambda: self._delete_folder(col_id, folder_path),
                    True,
                ),
            ]
            self._add_ranked_context_actions(menu, "collections_folder", delete_specs)

        elif data["type"] == "request":
            run_specs = [
                ("open_in_editor", "Open in Editor", lambda: self._load_request(data["id"]), False),
                ("run_now", "▶  Run Now", lambda: self._run_request(data["id"]), False),
            ]
            self._add_ranked_context_actions(menu, "collections_request", run_specs)
            menu.addSeparator()

            req_id = data.get("id")
            manage_specs = []
            if isinstance(req_id, int) and req_id > 0:
                manage_specs.append(
                    (
                        "show_api_spec",
                        "Show API Spec…",
                        lambda r=req_id: self._show_api_spec_for_request(r),
                        False,
                    )
                )
            else:
                logger.debug(
                    "CollectionsPanel: skipping Show API Spec action for invalid request id: %r",
                    req_id,
                )
            manage_specs.extend(
                [
                    ("rename", "Rename…", lambda: self._rename_request(data["id"], item), False),
                    ("duplicate", "Duplicate", lambda: self._duplicate_request(data["id"]), False),
                    (
                        "move_to_folder",
                        "Move to Folder…",
                        lambda: self._move_to_folder(data["id"]),
                        False,
                    ),
                ]
            )
            self._add_ranked_context_actions(menu, "collections_request", manage_specs)
            menu.addSeparator()

            delete_specs = [
                (
                    "delete_request",
                    "Delete Request",
                    lambda: self._delete_request(data["id"]),
                    True,
                ),
            ]
            self._add_ranked_context_actions(menu, "collections_request", delete_specs)

        menu.exec(self.tree.viewport().mapToGlobal(position))

    def _context_action_usage_count(self, context: str, action_id: str) -> int:
        tracker = getattr(self.window(), "_ui_usage_tracker", None)
        if tracker is None:
            return 0
        try:
            return tracker.get_count(
                category="context_menu",
                context=context,
                element_id=f"action.{action_id}",
            )
        except Exception:
            logger.debug(
                "Failed to get context action usage for %s/%s", context, action_id, exc_info=True
            )
            return 0

    def _record_context_action_usage(self, context: str, action_id: str) -> None:
        tracker = getattr(self.window(), "_ui_usage_tracker", None)
        if tracker is None:
            return
        try:
            tracker.record(
                f"action.{action_id}",
                category="context_menu",
                context=context,
            )
        except Exception:
            logger.debug(
                "Failed to record context action usage for %s/%s", context, action_id, exc_info=True
            )

    def _run_context_action(self, context: str, action_id: str, callback) -> None:
        self._record_context_action_usage(context, action_id)
        callback()

    def _ordered_context_actions(self, context: str, action_specs: list[tuple]) -> list[tuple]:
        """Sort non-destructive actions by usage while keeping destructive actions last."""
        safe = []
        destructive = []
        for idx, spec in enumerate(action_specs):
            action_id, label, callback, is_destructive = spec
            if is_destructive:
                destructive.append((idx, spec))
                continue
            count = self._context_action_usage_count(context, action_id)
            safe.append((-count, idx, spec))
        safe.sort(key=lambda row: (row[0], row[1]))
        return [row[2] for row in safe] + [row[1] for row in destructive]

    def _add_ranked_context_actions(
        self, menu: QMenu, context: str, action_specs: list[tuple]
    ) -> None:
        added_destructive_separator = False
        for action_id, label, callback, is_destructive in self._ordered_context_actions(
            context, action_specs
        ):
            if is_destructive and not added_destructive_separator:
                menu.addSeparator()
                added_destructive_separator = True
            action = QAction(label, self)
            action.triggered.connect(
                lambda _checked=False, aid=action_id, cb=callback: self._run_context_action(
                    context, aid, cb
                )
            )
            menu.addAction(action)

    # ── API spec helpers ─────────────────────────────────────────────
    def _show_spec_dialog(self, title: str, variants: dict) -> None:
        """Show API spec dialog and track lifecycle to avoid GC issues."""
        dlg = ApiSpecDialog(self, title=title)
        dlg.set_variants(variants)
        self._dialog_registry.register(dlg)
        dlg.show()
        try:
            dlg.raise_()
            dlg.activateWindow()
        except Exception:
            logger.debug(
                "CollectionsPanel: unable to raise/activate API spec dialog", exc_info=True
            )

    def _show_api_spec_for_collection(self, collection_id: int) -> None:
        """Open ApiSpecDialog showing OpenAPI and Postman variants for a collection."""
        try:
            payload = self._api_spec_service.build_collection_payload(collection_id)
        except ValueError as exc:
            ErrorPresenter.warning(self, str(exc), title="Invalid Collection")
            return
        except Exception as exc:
            logger.exception(
                "CollectionsPanel: failed to build collection API spec id=%s", collection_id
            )
            ErrorPresenter.warning(self, f"Failed to load collection: {exc}", title="Export Error")
            return

        self._show_spec_dialog(payload.title, payload.variants)

    def _show_api_spec_for_request(self, request_id: int) -> None:
        """Open ApiSpecDialog showing cURL and (optionally) mini OpenAPI for a request."""
        try:
            payload = self._api_spec_service.build_request_payload(request_id)
        except ValueError as exc:
            ErrorPresenter.warning(self, str(exc), title="Not Found")
            return
        except Exception as exc:
            logger.exception("CollectionsPanel: failed to build request API spec id=%s", request_id)
            ErrorPresenter.warning(self, f"Failed to load request: {exc}", title="Export Error")
            return

        self._show_spec_dialog(payload.title, payload.variants)
