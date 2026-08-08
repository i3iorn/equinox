"""Collection facade for collection-panel workflows.

This module defines a small application-layer boundary for collection-panel
operations so GUI code does not construct storage managers or reach through
manager internals.
"""

from __future__ import annotations

from typing import Any

from equinox.core.request import Request
from equinox.storage import CollectionManager, Database


class CollectionFacade:
    """Facade over collection storage operations used by the GUI collection panel."""

    def __init__(
        self,
        db: Database,
        collection_manager: CollectionManager | None = None,
    ) -> None:
        self._manager = collection_manager or CollectionManager(db)

    # ── Read helpers ──────────────────────────────────────────────────

    def list_collections(self) -> list[dict[str, Any]]:
        return list(self._manager.list_collections())

    def list_folders(self, collection_id: int) -> list[str]:
        return list(self._manager.list_folders(collection_id))

    def list_requests(self, collection_id: int) -> list[dict[str, Any]]:
        return list(self._manager.list_requests(collection_id))

    def get_collection(self, collection_id: int) -> dict[str, Any] | None:
        return self._manager.get_collection(collection_id)

    def get_request(self, request_id: int) -> Request | None:
        return self._manager.get_request(request_id)

    def get_request_location(self, request_id: int) -> tuple[int, str | None] | None:
        row = self._manager.db.fetchone(
            "SELECT collection_id, folder FROM requests WHERE id=?",
            (request_id,),
        )
        if not row:
            return None
        return row["collection_id"], row["folder"] or None

    def list_group_request_ids(self, collection_id: int, folder: str | None) -> list[int]:
        rows = self._manager.db.fetchall(
            "SELECT id FROM requests "
            "WHERE collection_id=? AND COALESCE(folder, '') = COALESCE(?, '') "
            "ORDER BY sort_order, id",
            (collection_id, folder),
        )
        return [row["id"] for row in rows]

    # ── Collection/request lifecycle ──────────────────────────────────

    def create_collection(self, name: str) -> int:
        return self._manager.create_collection(name)

    def rename_collection(self, collection_id: int, new_name: str) -> None:
        self._manager.rename_collection(collection_id, new_name)

    def delete_collection(self, collection_id: int) -> None:
        self._manager.delete_collection(collection_id)

    def save_request(self, request: Request, *, collection_id: int, name: str) -> int:
        return self._manager.save_request(request, collection_id=collection_id, name=name)

    def rename_request(self, request_id: int, new_name: str) -> None:
        self._manager.rename_request(request_id, new_name)

    def duplicate_request(self, request_id: int) -> int:
        return self._manager.duplicate_request(request_id)

    def delete_request(self, request_id: int) -> None:
        self._manager.delete_request(request_id)

    # ── Folder operations ──────────────────────────────────────────────

    def create_folder(self, collection_id: int, folder_path: str) -> None:
        self._manager.create_folder(collection_id, folder_path)

    def rename_folder(self, collection_id: int, old_path: str, new_path: str) -> None:
        self._manager.rename_folder(collection_id, old_path, new_path)

    def delete_folder(self, collection_id: int, folder_path: str, *, move_to_root: bool) -> None:
        self._manager.delete_folder(collection_id, folder_path, move_to_root=move_to_root)

    # ── Move/reorder operations ───────────────────────────────────────

    def move_request_to_folder(self, request_id: int, folder: str | None) -> None:
        self._manager.move_request_to_folder(request_id, folder)

    def move_request_to_collection(
        self,
        request_id: int,
        collection_id: int,
        folder: str | None,
    ) -> None:
        self._manager.move_request_to_collection(request_id, collection_id, folder)

    def reorder_requests(self, ordered_ids: list[int]) -> None:
        self._manager.reorder_requests(ordered_ids)

    def reorder_request_before_target(self, dragged_id: int, target_id: int) -> None:
        target_loc = self.get_request_location(target_id)
        dragged_loc = self.get_request_location(dragged_id)
        if not target_loc or not dragged_loc:
            return

        t_col, t_folder = target_loc
        d_col, d_folder = dragged_loc

        if d_col != t_col or d_folder != t_folder:
            if d_col != t_col:
                self.move_request_to_collection(dragged_id, t_col, t_folder)
            else:
                self.move_request_to_folder(dragged_id, t_folder)

        ordered_ids = [
            rid for rid in self.list_group_request_ids(t_col, t_folder) if rid != dragged_id
        ]
        try:
            insert_at = ordered_ids.index(target_id)
        except ValueError:
            insert_at = len(ordered_ids)
        ordered_ids.insert(insert_at, dragged_id)
        self.reorder_requests(ordered_ids)

    # ── Sorting ────────────────────────────────────────────────────────

    def sort_requests_alphabetically(self, collection_id: int, folder: str | None) -> None:
        self._manager.sort_requests_alphabetically(collection_id, folder)

    def sort_requests_by_method(self, collection_id: int, folder: str | None) -> None:
        self._manager.sort_requests_by_method(collection_id, folder)

    # ── Hierarchical auth ──────────────────────────────────────────────

    def get_collection_auth(self, collection_id: int) -> Any:
        return self._manager.get_collection_auth(collection_id)

    def set_collection_auth(self, collection_id: int, auth: Any) -> None:
        self._manager.set_collection_auth(collection_id, auth)

    def get_folder_auth(self, collection_id: int, folder_path: str) -> Any:
        return self._manager.get_folder_auth(collection_id, folder_path)

    def set_folder_auth(self, collection_id: int, folder_path: str, auth: Any) -> None:
        self._manager.set_folder_auth(collection_id, folder_path, auth)
