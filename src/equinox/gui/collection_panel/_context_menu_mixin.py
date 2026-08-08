"""Context menu rendering and ranking helpers for collections tree."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any
from typing import cast
from typing import Protocol

from PyQt6.QtCore import QPoint
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QMenu
from PyQt6.QtWidgets import QTreeWidget
from PyQt6.QtWidgets import QTreeWidgetItem
from PyQt6.QtWidgets import QWidget

logger = logging.getLogger(__name__)

ContextAction = tuple[str, str, Callable[[], None], bool]


class CollectionsPanelProtocol(Protocol):
    tree: QTreeWidget
    _delete_collection: Callable[[int], None]
    _new_request_in_collection: Callable[[int], None]
    _create_folder_in_collection: Callable[[int], None]
    _show_api_spec_for_collection: Callable[[int], None]
    _rename_collection: Callable[[int, QTreeWidgetItem], None]
    _manage_collection: Callable[[int], None]
    _set_collection: Callable[[int], None]
    _clear_collection: Callable[[int], None]
    _col_id_to_item: dict[int, QTreeWidgetItem]
    _delete_folder: Callable[[int, str], None]
    _new_request_in_folder: Callable[[int, str], None]
    _create_subfolder: Callable[[int, str], None]
    _set_folder_auth: Callable[[int, str], None]
    _clear_folder_auth: Callable[[int, str], None]
    _rename_folder: Callable[[int, str, QTreeWidgetItem], None]
    _delete_request: Callable[[int], None]
    _load_request: Callable[[int], None]
    _run_request: Callable[[int], None]
    _rename_request: Callable[[int, QTreeWidgetItem], None]
    _duplicate_request: Callable[[int], None]
    _move_to_folder: Callable[[int], None]
    _sort_group: Callable[[int, str | None, str], None]
    window: Callable[[], QWidget]
    _manage_variables: Callable[[int], None]
    _set_collection_auth: Callable[[int], None]
    _clear_collection_auth: Callable[[int], None]
    _col_id_for_item: Callable[[QTreeWidgetItem], int | None]
    _show_api_spec_for_request: Callable[[int], None]


class _CollectionsContextMenuMixin(CollectionsPanelProtocol):
    """Behavior for collection-tree context menus and usage-based ranking."""

    def _show_context_menu(self, position: QPoint) -> None:
        """Render a context menu for the item at the given position."""
        item = self._safe_item_lookup(position)
        if item is None:
            return

        data = self._safe_item_data(item)
        if data is None:
            return

        menu = QMenu()

        handlers: dict[str, Callable[[QMenu, QTreeWidgetItem, dict[str, Any]], None]] = {
            "collection": self._build_collection_menu,
            "folder": self._build_folder_menu,
            "request": self._build_request_menu,
        }

        node_type = data.get("type")
        handler = handlers.get(node_type) if isinstance(node_type, str) else None
        if handler is None:
            logger.debug("Unknown item type in context menu: %r", data.get("type"))
            return

        handler(menu, item, data)
        viewport = self.tree.viewport()
        if viewport is None:
            return
        menu.exec(viewport.mapToGlobal(position))

    def _safe_item_lookup(self, position: QPoint) -> QTreeWidgetItem | None:
        """Return the tree item at the given position, or None if invalid."""
        item = self.tree.itemAt(position)
        if not isinstance(item, QTreeWidgetItem):
            return None
        return item

    def _safe_item_data(self, item: QTreeWidgetItem) -> dict[str, Any] | None:
        """Return validated item data."""
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
        specs = [
            (
                "new_request",
                "New Request...",
                lambda: self._new_request_in_collection(col_id),
                False,
            ),
            (
                "add_folder",
                "Add Folder...",
                lambda: self._create_folder_in_collection(col_id),
                False,
            ),
        ]
        self._add_ranked_context_actions(menu, "collections_collection", specs)

    def _add_collection_manage_actions(
        self,
        menu: QMenu,
        col_id: int,
        item: QTreeWidgetItem,
    ) -> None:
        specs: list[ContextAction] = []

        if col_id > 0:
            specs.append(
                (
                    "show_api_spec",
                    "Show API Spec...",
                    lambda cid=col_id: self._show_api_spec_for_collection(cid),  # type: ignore[misc]
                    False,
                ),
            )
        else:
            logger.debug("Skipping Show API Spec for invalid collection id: %r", col_id)

        specs.extend(
            [
                ("rename", "Rename...", lambda: self._rename_collection(col_id, item), False),
                (
                    "manage_variables",
                    "Manage Variables...",
                    lambda: self._manage_variables(col_id),
                    False,
                ),
                ("set_auth", "Set Auth", lambda: self._set_collection_auth(col_id), False),
                (
                    "clear_auth",
                    "Clear Auth...",
                    lambda: self._clear_collection_auth(col_id),
                    False,
                ),
            ],
        )

        self._add_ranked_context_actions(menu, "collections_collection", specs)

    def _build_folder_menu(self, menu: QMenu, item: QTreeWidgetItem, data: dict[str, Any]) -> None:
        col_id = self._col_id_for_item(item)
        folder_path = data.get("path")
        if not isinstance(folder_path, str):
            logger.debug("Invalid folder path: %r", folder_path)
            return

        if not isinstance(col_id, int):
            logger.debug("Invalid folder collection id: %r", col_id)
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
                    "Delete Folder...",
                    lambda: self._delete_folder(col_id, folder_path),
                    True,
                ),
            ],
        )

    def _add_folder_create_actions(self, menu: QMenu, col_id: int, folder_path: str) -> None:
        specs = [
            (
                "new_request_here",
                "New Request Here...",
                lambda: self._new_request_in_folder(col_id, folder_path),
                False,
            ),
            (
                "add_subfolder",
                "Add Subfolder...",
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
        specs = [
            (
                "set_auth",
                "Set Auth...",
                lambda: self._set_folder_auth(col_id, folder_path),
                False,
            ),
            (
                "clear_auth",
                "Clear Auth",
                lambda: self._clear_folder_auth(col_id, folder_path),
                False,
            ),
            (
                "rename_folder",
                "Rename Folder...",
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
        specs = [
            ("open_in_editor", "Open in Editor", lambda: self._load_request(req_id), False),
            ("run_now", "ÔûÂ  Run Now", lambda: self._run_request(req_id), False),
        ]
        self._add_ranked_context_actions(menu, "collections_request", specs)

    def _add_request_manage_actions(self, menu: QMenu, req_id: int, item: QTreeWidgetItem) -> None:
        specs: list[ContextAction] = []

        if isinstance(req_id, int) and req_id > 0:
            specs.append(
                (
                    "show_api_spec",
                    "Show API Spec...",
                    lambda r=req_id: self._show_api_spec_for_request(r),  # type: ignore[misc]
                    False,
                ),
            )
        else:
            logger.debug("Skipping Show API Spec for invalid request id: %r", req_id)

        specs.extend(
            [
                ("rename", "Rename...", lambda: self._rename_request(req_id, item), False),
                ("duplicate", "Duplicate", lambda: self._duplicate_request(req_id), False),
                (
                    "move_to_folder",
                    "Move to Folder...",
                    lambda: self._move_to_folder(req_id),
                    False,
                ),
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

        specs = [
            (
                "sort_alpha",
                "Sort A ÔåÆ Z",
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
        specs: list[ContextAction],
    ) -> None:
        self._add_ranked_context_actions(menu, ranking_key, specs)

    def _context_action_usage_count(self, context: str, action_id: str) -> int:
        tracker = getattr(self.window(), "_ui_usage_tracker", None)
        if tracker is None:
            return 0
        try:
            return cast(
                int,
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

    def _run_context_action(
        self,
        context: str,
        action_id: str,
        callback: Callable[[], None],
    ) -> None:
        self._record_context_action_usage(context, action_id)
        callback()

    def _ordered_context_actions(
        self,
        context: str,
        action_specs: list[ContextAction],
    ) -> list[ContextAction]:
        """Sort non-destructive actions by usage while keeping destructive actions last."""
        safe: list[tuple[int, int, ContextAction]] = []
        destructive: list[tuple[int, ContextAction]] = []
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
        action_specs: list[ContextAction],
    ) -> None:
        added_destructive_separator = False
        for action_id, label, callback, is_destructive in self._ordered_context_actions(
            context,
            action_specs,
        ):
            if is_destructive and not added_destructive_separator:
                menu.addSeparator()
                added_destructive_separator = True
            action = QAction(label, cast(QWidget, self))
            action.triggered.connect(
                lambda _checked=False, aid=action_id, cb=callback: self._run_context_action(
                    context,
                    aid,
                    cb,
                ),
            )
            menu.addAction(action)
