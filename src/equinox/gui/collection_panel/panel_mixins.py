"""Mixins for collections panel tree behavior, context menus, and API-spec dialogs."""

# mypy: disable-error-code=attr-defined
from __future__ import annotations

import logging
from typing import Any
from typing import cast

from equinox.gui.dialogs.api_spec_dialog import ApiSpecDialog
from equinox.gui.error_presenter import ErrorPresenter
from equinox.gui.theme import Colors
from PyQt6.QtCore import QPoint
from PyQt6.QtCore import Qt
from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QAction
from PyQt6.QtGui import QColor
from PyQt6.QtGui import QKeySequence
from PyQt6.QtGui import QShortcut
from PyQt6.QtWidgets import QMenu
from PyQt6.QtWidgets import QTreeWidgetItem
from PyQt6.QtWidgets import QWidget

logger = logging.getLogger(__name__)

_AUTO_REFRESH_INTERVAL_MS = 30_000


def _as_qwidget(host: Any) -> QWidget:
    """Return mixin host as QWidget for Qt APIs requiring QObject/QWidget parent."""
    return cast(QWidget, host)


class _CollectionsSelectionFilterMixin:
    """Selection, keyboard shortcuts, and filter/expansion behavior."""

    _pre_filter_expansion: dict[str, set[Any]] | None
    _programmatic_expand: bool

    def _setup_keyboard_shortcuts(self) -> None:
        """Install keyboard shortcuts on the tree widget."""
        enter = QShortcut(QKeySequence(Qt.Key.Key_Return), self.tree)
        enter.activated.connect(self._kbd_open)

        delete = QShortcut(QKeySequence(Qt.Key.Key_Delete), self.tree)
        delete.activated.connect(self._kbd_delete)

        f2 = QShortcut(QKeySequence(Qt.Key.Key_F2), self.tree)
        f2.activated.connect(self._kbd_rename)

        dup = QShortcut(QKeySequence("Ctrl+D"), self.tree)
        dup.activated.connect(self._kbd_duplicate)

    def _selected_data(self) -> tuple[QTreeWidgetItem | None, dict[str, Any] | None]:
        item = self.tree.currentItem()
        if not item:
            return None, None
        data = item.data(0, Qt.ItemDataRole.UserRole)
        return item, data if isinstance(data, dict) else None

    def _kbd_open(self) -> None:
        _item, data = self._selected_data()
        if data and data["type"] == "request":
            self._load_request(data["id"])

    def _kbd_delete(self) -> None:
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

    def _kbd_rename(self) -> None:
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

    def _kbd_duplicate(self) -> None:
        _item, data = self._selected_data()
        if data and data["type"] == "request":
            self._duplicate_request(data["id"])

    def _apply_filter(self, text: str) -> None:
        """Show/hide tree items based on filter text."""
        needle = text.strip().lower()

        if needle:
            if self._pre_filter_expansion is None:
                self._pre_filter_expansion = self._get_expansion_state()

            self._programmatic_expand = True
            try:
                for i in range(self.tree.topLevelItemCount()):
                    col_item = self.tree.topLevelItem(i)
                    if col_item is None:
                        continue
                    col_visible = self._filter_subtree(col_item, needle)
                    if needle in col_item.text(0).lower():
                        col_visible = True
                    col_item.setHidden(not col_visible)
                    if col_visible:
                        col_item.setExpanded(True)
            finally:
                self._programmatic_expand = False
            return

        saved = self._pre_filter_expansion
        self._pre_filter_expansion = None

        self._programmatic_expand = True
        try:
            for i in range(self.tree.topLevelItemCount()):
                col_item = self.tree.topLevelItem(i)
                if col_item is None:
                    continue
                col_item.setHidden(False)
                self._unhide_subtree(col_item)

                data = col_item.data(0, Qt.ItemDataRole.UserRole) or {}
                col_id = data.get("id") if isinstance(data, dict) else None
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
            if child is None:
                continue
            child.setHidden(False)
            _CollectionsSelectionFilterMixin._unhide_subtree(child)

    def _filter_subtree(self, parent: QTreeWidgetItem, needle: str) -> bool:
        """Recursively show/hide children. Returns True if any child is visible."""
        any_visible = False
        for j in range(parent.childCount()):
            child = parent.child(j)
            if child is None:
                continue
            data = child.data(0, Qt.ItemDataRole.UserRole) or {}
            if data.get("type") == "folder":
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
        folder_set: set[str],
    ) -> None:
        """Recursively restore folder expansion from *folder_set*."""
        for j in range(parent.childCount()):
            child = parent.child(j)
            if child is None:
                continue
            cdata = child.data(0, Qt.ItemDataRole.UserRole) or {}
            if cdata.get("type") == "folder":
                key = f"{col_id}:{cdata.get('path', '')}"
                child.setExpanded(key in folder_set)
                _CollectionsSelectionFilterMixin._restore_folder_expansion(
                    child,
                    col_id,
                    folder_set,
                )

    def _on_item_expanded(self, item: QTreeWidgetItem) -> None:
        if self._programmatic_expand or self._pre_filter_expansion is None:
            return
        self._update_pre_filter_expansion(item, expanded=True)

    def _on_item_collapsed(self, item: QTreeWidgetItem) -> None:
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
            return

        if data.get("type") == "folder":
            col_id = self._col_id_for_item(item)
            path = data.get("path", "")
            if col_id is None:
                return
            key = f"{col_id}:{path}"
            if expanded:
                saved["folders"].add(key)
            else:
                saved["folders"].discard(key)

    def _on_item_double_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        del column
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return
        if data["type"] == "request":
            request = self._collection_facade.get_request(data["id"])
            if request:
                self.request_selected.emit(request)
        elif data["type"] == "collection":
            self._rename_collection(data["id"], item)
        elif data["type"] == "folder":
            col_id = self._col_id_for_item(item)
            if col_id is not None:
                self._rename_folder(col_id, data["path"], item)


class _CollectionsRefreshTreeMixin:
    """Auto-refresh and tree materialization behavior."""

    _pre_filter_expansion: dict[str, set[Any]] | None
    _programmatic_expand: bool

    def _setup_auto_refresh(self) -> None:
        self.refresh_timer = QTimer(_as_qwidget(self))
        self.refresh_timer.timeout.connect(self._refresh_if_visible)
        self.refresh_timer.start(_AUTO_REFRESH_INTERVAL_MS)

    def _refresh_if_visible(self) -> None:
        if self.isVisible():
            self.refresh()

    def _toggle_auto_refresh(self, state: int) -> None:
        self.auto_refresh_enabled = Qt.CheckState(state) == Qt.CheckState.Checked
        if self.auto_refresh_enabled:
            self.refresh_timer.start(_AUTO_REFRESH_INTERVAL_MS)
            return
        self.refresh_timer.stop()

    def _get_expansion_state(self) -> dict[str, set[Any]]:
        """Return expansion state for collections and folders."""
        state: dict[str, set[Any]] = {"collections": set(), "folders": set()}
        for i in range(self.tree.topLevelItemCount()):
            col_item = self.tree.topLevelItem(i)
            if col_item is None:
                continue
            data = col_item.data(0, Qt.ItemDataRole.UserRole) or {}
            col_id = data.get("id") if isinstance(data, dict) else None
            if col_item.isExpanded() and col_id is not None:
                state["collections"].add(col_id)
            self._collect_folder_expansion(col_item, col_id, state["folders"])
        return state

    def _collect_folder_expansion(
        self,
        parent: QTreeWidgetItem,
        col_id: int | None,
        folder_set: set[str],
    ) -> None:
        for j in range(parent.childCount()):
            child = parent.child(j)
            if child is None:
                continue
            cdata = child.data(0, Qt.ItemDataRole.UserRole) or {}
            if cdata.get("type") == "folder" and child.isExpanded() and col_id is not None:
                folder_set.add(f"{col_id}:{cdata.get('path', '')}")
            self._collect_folder_expansion(child, col_id, folder_set)

    def refresh(self) -> None:
        """Refresh collections tree, preserving expansion state."""
        exp_state = self._select_expansion_state()

        self._programmatic_expand = True
        try:
            self._rebuild_tree(exp_state)
        finally:
            self._programmatic_expand = False

        self._apply_filter(self._filter_input.text())

    def _select_expansion_state(self) -> dict[str, Any]:
        saved = self._pre_filter_expansion
        if saved is not None:
            return saved
        return self._get_expansion_state()

    def _rebuild_tree(self, exp_state: dict[str, Any]) -> None:
        self.tree.clear()
        collections = self._collection_facade.list_collections()

        for col in collections:
            col_id = col.get("id")
            if not self._is_valid_collection_id(col_id):
                self._log_invalid_collection(col)
                continue

            col_item = self._create_collection_item(col)
            self.tree.addTopLevelItem(col_item)

            folder_items = self._materialize_folders(col_id, col_item, exp_state)
            folder_counts, col_root_count = self._populate_requests(
                col_id,
                col_item,
                folder_items,
                exp_state,
            )

            self._apply_count_badges(col, col_item, folder_items, folder_counts, col_root_count)
            self._restore_collection_expansion(col_id, col_item, exp_state)

    def _is_valid_collection_id(self, col_id: Any) -> bool:
        return isinstance(col_id, int) and col_id > 0 and not isinstance(col_id, bool)

    def _log_invalid_collection(self, col: dict[str, Any]) -> None:
        logger.warning(
            "Skipping collection with invalid id=%r name=%r",
            col.get("id"),
            col.get("name"),
        )

    def _create_collection_item(self, col: dict[str, Any]) -> QTreeWidgetItem:
        item = QTreeWidgetItem([col["name"]])
        font = item.font(0)
        font.setBold(True)
        item.setFont(0, font)
        item.setData(0, Qt.ItemDataRole.UserRole, {"type": "collection", "id": col["id"]})
        return item

    def _materialize_folders(
        self,
        col_id: int,
        col_item: QTreeWidgetItem,
        exp_state: dict[str, Any],
    ) -> dict[str, QTreeWidgetItem]:
        folder_items: dict[str, QTreeWidgetItem] = {}
        for folder_path in self._collection_facade.list_folders(col_id):
            self._ensure_folder_item(
                col_item,
                folder_path,
                folder_items,
                exp_state["folders"],
                col_id,
            )
        return folder_items

    def _populate_requests(
        self,
        col_id: int,
        col_item: QTreeWidgetItem,
        folder_items: dict[str, QTreeWidgetItem],
        exp_state: dict[str, Any],
    ) -> tuple[dict[str, int], int]:
        folder_counts: dict[str, int] = {}
        col_root_count = 0

        for req in self._collection_facade.list_requests(col_id):
            parent, folder_path = self._resolve_parent_folder(
                req,
                col_item,
                folder_items,
                exp_state,
                col_id,
            )

            if folder_path:
                folder_counts[folder_path] = folder_counts.get(folder_path, 0) + 1
            else:
                col_root_count += 1

            req_item = self._create_request_item(req, folder_path)
            parent.addChild(req_item)

        return folder_counts, col_root_count

    def _resolve_parent_folder(
        self,
        req: dict[str, Any],
        col_item: QTreeWidgetItem,
        folder_items: dict[str, QTreeWidgetItem],
        exp_state: dict[str, Any],
        col_id: int,
    ) -> tuple[QTreeWidgetItem, str]:
        folder_path = (req.get("folder") or "").strip()
        if folder_path:
            parent = self._ensure_folder_item(
                col_item,
                folder_path,
                folder_items,
                exp_state["folders"],
                col_id,
            )
        else:
            parent = col_item
        return parent, folder_path

    def _create_request_item(self, req: dict[str, Any], folder_path: str) -> QTreeWidgetItem:
        method = req["method"]
        color = Colors.METHOD.get(method, Colors.MUTED)

        display_name = req["name"]
        if folder_path and display_name.startswith(folder_path + "/"):
            display_name = display_name[len(folder_path) + 1 :]

        item = QTreeWidgetItem([f"{method}  {display_name}"])
        item.setForeground(0, QColor(color))
        item.setData(0, Qt.ItemDataRole.UserRole, {"type": "request", "id": req["id"]})
        return item

    def _apply_count_badges(
        self,
        col: dict[str, Any],
        col_item: QTreeWidgetItem,
        folder_items: dict[str, QTreeWidgetItem],
        folder_counts: dict[str, int],
        col_root_count: int,
    ) -> None:
        total = col_root_count + sum(folder_counts.values())
        if total:
            col_item.setText(0, f"{col['name']}  ({total})")

        for path, item in folder_items.items():
            leaf = path.split("/")[-1]
            count = folder_counts.get(path, 0)
            item.setText(0, f"📁 {leaf}  ({count})" if count else f"📁 {leaf}")

    def _restore_collection_expansion(
        self,
        col_id: int,
        col_item: QTreeWidgetItem,
        exp_state: dict[str, Any],
    ) -> None:
        col_item.setExpanded(col_id in exp_state["collections"])

    @staticmethod
    def _ensure_folder_item(
        col_item: QTreeWidgetItem,
        folder_path: str,
        cache: dict[str, QTreeWidgetItem],
        expansion_state: set[str] | None = None,
        col_id: int | None = None,
    ) -> QTreeWidgetItem:
        """Return (creating if needed) a folder tree item for *folder_path*."""
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
                should_expand = (
                    expansion_state is not None
                    and col_id is not None
                    and f"{col_id}:{accumulated}" in expansion_state
                )
                folder_item.setExpanded(should_expand)
                cache[accumulated] = folder_item
            current_parent = cache[accumulated]

        return current_parent


class _CollectionsContextMenuMixin:
    """Context-menu rendering and usage-ranked action ordering."""

    def _show_context_menu(self, position: QPoint) -> None:
        """Render a context menu for the item at the given position."""
        item = self._safe_item_lookup(position)
        if item is None:
            return

        data = self._safe_item_data(item)
        if data is None:
            return

        menu = QMenu(_as_qwidget(self))
        handlers = {
            "collection": self._build_collection_menu,
            "folder": self._build_folder_menu,
            "request": self._build_request_menu,
        }

        item_type = data.get("type")
        if not isinstance(item_type, str):
            return
        handler = handlers.get(item_type)
        if handler is None:
            logger.debug("Unknown item type in context menu: %r", item_type)
            return

        handler(menu, item, data)
        menu.exec(self.tree.viewport().mapToGlobal(position))

    def _safe_item_lookup(self, position: QPoint) -> QTreeWidgetItem | None:
        item = self.tree.itemAt(position)
        if not isinstance(item, QTreeWidgetItem):
            return None
        return item

    def _safe_item_data(self, item: QTreeWidgetItem) -> dict[str, Any] | None:
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(data, dict):
            return None
        return data

    def _build_collection_menu(
        self,
        menu: QMenu,
        item: QTreeWidgetItem,
        data: dict[str, Any],
    ) -> None:
        col_id = data.get("id")
        if not isinstance(col_id, int):
            logger.debug("Invalid collection id: %r", col_id)
            return

        self._add_collection_create_actions(menu, col_id)
        menu.addSeparator()

        self._add_collection_manage_actions(menu, col_id, item)
        menu.addSeparator()

        self._add_sort_menu(menu, col_id, None, "collections_collection_sort")
        menu.addSeparator()

        self._add_delete_actions(
            menu,
            "collections_collection",
            [
                (
                    "delete_collection",
                    "Delete Collection",
                    lambda: self._delete_collection(col_id),
                    True,
                ),
            ],
        )

    def _add_collection_create_actions(self, menu: QMenu, col_id: int) -> None:
        specs: list[tuple[str, str, Any, bool]] = [
            ("new_request", "New Request…", lambda: self._new_request_in_collection(col_id), False),
            ("add_folder", "Add Folder…", lambda: self._create_folder_in_collection(col_id), False),
        ]
        self._add_ranked_context_actions(menu, "collections_collection", specs)

    def _add_collection_manage_actions(
        self,
        menu: QMenu,
        col_id: int,
        item: QTreeWidgetItem,
    ) -> None:
        specs: list[tuple[str, str, Any, bool]] = []

        if col_id > 0:
            specs.append(
                (
                    "show_api_spec",
                    "Show API Spec…",
                    lambda cid=col_id: self._show_api_spec_for_collection(cid),
                    False,
                ),
            )
        else:
            logger.debug("Skipping Show API Spec for invalid collection id: %r", col_id)

        specs.extend(
            [
                ("rename", "Rename…", lambda: self._rename_collection(col_id, item), False),
                (
                    "manage_variables",
                    "Manage Variables…",
                    lambda: self._manage_variables(col_id),
                    False,
                ),
                ("set_auth", "Set Auth…", lambda: self._set_collection_auth(col_id), False),
                ("clear_auth", "Clear Auth", lambda: self._clear_collection_auth(col_id), False),
            ],
        )

        self._add_ranked_context_actions(menu, "collections_collection", specs)

    def _build_folder_menu(self, menu: QMenu, item: QTreeWidgetItem, data: dict[str, Any]) -> None:
        col_id = self._col_id_for_item(item)
        folder_path = data.get("path")

        if not isinstance(col_id, int):
            logger.debug("Invalid folder collection id: %r", col_id)
            return
        if not isinstance(folder_path, str):
            logger.debug("Invalid folder path: %r", folder_path)
            return

        self._add_folder_create_actions(menu, col_id, folder_path)
        menu.addSeparator()

        self._add_sort_menu(menu, col_id, folder_path, "collections_folder_sort")
        menu.addSeparator()

        self._add_folder_manage_actions(menu, col_id, folder_path, item)
        menu.addSeparator()

        self._add_delete_actions(
            menu,
            "collections_folder",
            [
                (
                    "delete_folder",
                    "Delete Folder…",
                    lambda: self._delete_folder(col_id, folder_path),
                    True,
                ),
            ],
        )

    def _add_folder_create_actions(self, menu: QMenu, col_id: int, folder_path: str) -> None:
        specs: list[tuple[str, str, Any, bool]] = [
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
        self._add_ranked_context_actions(menu, "collections_folder", specs)

    def _add_folder_manage_actions(
        self,
        menu: QMenu,
        col_id: int,
        folder_path: str,
        item: QTreeWidgetItem,
    ) -> None:
        specs: list[tuple[str, str, Any, bool]] = [
            ("set_auth", "Set Auth…", lambda: self._set_folder_auth(col_id, folder_path), False),
            (
                "clear_auth",
                "Clear Auth",
                lambda: self._clear_folder_auth(col_id, folder_path),
                False,
            ),
            (
                "rename_folder",
                "Rename Folder…",
                lambda: self._rename_folder(col_id, folder_path, item),
                False,
            ),
        ]
        self._add_ranked_context_actions(menu, "collections_folder", specs)

    def _build_request_menu(self, menu: QMenu, item: QTreeWidgetItem, data: dict[str, Any]) -> None:
        req_id = data.get("id")
        if not isinstance(req_id, int):
            logger.debug("Invalid request id: %r", req_id)
            return

        self._add_request_run_actions(menu, req_id)
        menu.addSeparator()

        self._add_request_manage_actions(menu, req_id, item)
        menu.addSeparator()

        self._add_delete_actions(
            menu,
            "collections_request",
            [("delete_request", "Delete Request", lambda: self._delete_request(req_id), True)],
        )

    def _add_request_run_actions(self, menu: QMenu, req_id: int) -> None:
        specs: list[tuple[str, str, Any, bool]] = [
            ("open_in_editor", "Open in Editor", lambda: self._load_request(req_id), False),
            ("run_now", "▶  Run Now", lambda: self._run_request(req_id), False),
        ]
        self._add_ranked_context_actions(menu, "collections_request", specs)

    def _add_request_manage_actions(self, menu: QMenu, req_id: int, item: QTreeWidgetItem) -> None:
        specs: list[tuple[str, str, Any, bool]] = []

        if isinstance(req_id, int) and req_id > 0:
            specs.append(
                (
                    "show_api_spec",
                    "Show API Spec…",
                    lambda r=req_id: self._show_api_spec_for_request(r),
                    False,
                ),
            )
        else:
            logger.debug("Skipping Show API Spec for invalid request id: %r", req_id)

        specs.extend(
            [
                ("rename", "Rename…", lambda: self._rename_request(req_id, item), False),
                ("duplicate", "Duplicate", lambda: self._duplicate_request(req_id), False),
                ("move_to_folder", "Move to Folder…", lambda: self._move_to_folder(req_id), False),
            ],
        )

        self._add_ranked_context_actions(menu, "collections_request", specs)

    def _add_sort_menu(
        self,
        menu: QMenu,
        col_id: int,
        folder_path: str | None,
        ranking_key: str,
    ) -> None:
        sort_menu = menu.addMenu("Sort Requests")
        if sort_menu is None:
            return

        specs: list[tuple[str, str, Any, bool]] = [
            (
                "sort_alpha",
                "Sort A → Z",
                lambda: self._sort_group(col_id, folder_path, "alpha"),
                False,
            ),
            (
                "sort_method",
                "Sort by Method",
                lambda: self._sort_group(col_id, folder_path, "method"),
                False,
            ),
        ]

        self._add_ranked_context_actions(sort_menu, ranking_key, specs)

    def _add_delete_actions(
        self,
        menu: QMenu,
        ranking_key: str,
        specs: list[tuple[str, str, Any, bool]],
    ) -> None:
        self._add_ranked_context_actions(menu, ranking_key, specs)

    def _context_action_usage_count(self, context: str, action_id: str) -> int:
        tracker = getattr(self.window(), "_ui_usage_tracker", None)
        if tracker is None:
            return 0
        try:
            return int(
                tracker.get_count(
                    category="context_menu",
                    context=context,
                    element_id=f"action.{action_id}",
                ),
            )
        except Exception:
            logger.exception(
                "Failed to get context action usage for %s/%s",
                context,
                action_id,
                exc_info=True,
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
            logger.exception(
                "Failed to record context action usage for %s/%s",
                context,
                action_id,
                exc_info=True,
            )

    def _run_context_action(self, context: str, action_id: str, callback: Any) -> None:
        self._record_context_action_usage(context, action_id)
        callback()

    def _ordered_context_actions(
        self,
        context: str,
        action_specs: list[tuple[str, str, Any, bool]],
    ) -> list[tuple[str, str, Any, bool]]:
        """Sort non-destructive actions by usage while keeping destructive actions last."""
        safe: list[tuple[int, int, tuple[str, str, Any, bool]]] = []
        destructive: list[tuple[int, tuple[str, str, Any, bool]]] = []
        for idx, spec in enumerate(action_specs):
            action_id, _label, _callback, is_destructive = spec
            if is_destructive:
                destructive.append((idx, spec))
                continue
            count = self._context_action_usage_count(context, action_id)
            safe.append((-count, idx, spec))
        safe.sort(key=lambda row: (row[0], row[1]))
        return [row[2] for row in safe] + [row[1] for row in destructive]

    def _add_ranked_context_actions(
        self,
        menu: QMenu,
        context: str,
        action_specs: list[tuple[str, str, Any, bool]],
    ) -> None:
        added_destructive_separator = False
        for action_id, label, callback, is_destructive in self._ordered_context_actions(
            context,
            action_specs,
        ):
            if is_destructive and not added_destructive_separator:
                menu.addSeparator()
                added_destructive_separator = True
            action = QAction(label, _as_qwidget(self))
            action.triggered.connect(
                lambda _checked=False, aid=action_id, cb=callback: self._run_context_action(
                    context,
                    aid,
                    cb,
                ),
            )
            menu.addAction(action)


class _CollectionsApiSpecMixin:
    """API specification dialog helpers for collection/request nodes."""

    def _show_spec_dialog(self, title: str, variants: dict[str, str]) -> None:
        """Show API spec dialog and track lifecycle to avoid GC issues."""
        dlg = ApiSpecDialog(self, title=title)
        dlg.set_variants(variants)
        self._dialog_registry.register(dlg)
        dlg.show()
        try:
            dlg.raise_()
            dlg.activateWindow()
        except Exception:
            logger.exception(
                "CollectionsPanel: unable to raise/activate API spec dialog",
                exc_info=True,
            )

    def _show_api_spec_for_collection(self, collection_id: int) -> None:
        """Open ApiSpecDialog with OpenAPI and Postman variants for a collection."""
        try:
            payload = self._api_spec_service.build_collection_payload(collection_id)
        except ValueError as exc:
            ErrorPresenter.warning(self, str(exc), title="Invalid Collection")
            return
        except Exception as exc:
            logger.exception(
                "CollectionsPanel: failed to build collection API spec id=%s",
                collection_id,
            )
            ErrorPresenter.warning(self, f"Failed to load collection: {exc}", title="Export Error")
            return

        self._show_spec_dialog(payload.title, payload.variants)

    def _show_api_spec_for_request(self, request_id: int) -> None:
        """Open ApiSpecDialog with cURL and optional mini OpenAPI for a request."""
        try:
            payload = self._api_spec_service.build_request_payload(request_id)
        except ValueError as exc:
            ErrorPresenter.warning(self, str(exc), title="Not Found")
            return
        except Exception as exc:
            logger.exception(
                "CollectionsPanel: failed to build request API spec id=%s",
                request_id,
            )
            ErrorPresenter.warning(self, f"Failed to load request: {exc}", title="Export Error")
            return

        self._show_spec_dialog(payload.title, payload.variants)
