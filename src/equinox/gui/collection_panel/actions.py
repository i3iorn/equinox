"""Action methods mixin for CollectionsPanel."""
from typing import Any

# mypy: disable-error-code=attr-defined

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QInputDialog,
    QMessageBox,
)

from equinox.core.request import Request
from equinox.gui.error_presenter import ErrorPresenter


class _CollectionsActionsMixin:
    """Mixin providing all action/handler methods for CollectionsPanel.

    Expects ``self.db``, ``self._tree`` (or ``self.tree``), signals
    ``self.request_selected``, ``self.request_run``, and
    ``self.collections_changed`` to be available on the host class.
    """

    def _load_request(self, request_id: int):
        request = self._collection_facade.get_request(request_id)
        if request:
            self.request_selected.emit(request)

    def _run_request(self, request_id: int):
        """Load and immediately fire the request without opening the editor."""
        request = self._collection_facade.get_request(request_id)
        if request:
            self.request_run.emit(request)

    def _rename_collection(self, collection_id: int, item):
        old_name = item.text(0)
        new_name, ok = QInputDialog.getText(self, "Rename Collection", "New name:", text=old_name)
        if not ok or not new_name.strip() or new_name.strip() == old_name:
            return
        try:
            self._collection_facade.rename_collection(collection_id, new_name.strip())
            self.refresh()
            self.collections_changed.emit()
        except Exception as exc:
            ErrorPresenter.error(self, str(exc))

    def _rename_request(self, request_id: int, item):
        # Strip method prefix from displayed name
        old_display = item.text(0)
        # "GET  My Request" → "My Request"
        parts = old_display.split("  ", 1)
        old_name = parts[1] if len(parts) > 1 else old_display
        new_name, ok = QInputDialog.getText(self, "Rename Request", "New name:", text=old_name)
        if not ok or not new_name.strip() or new_name.strip() == old_name:
            return
        try:
            self._collection_facade.rename_request(request_id, new_name.strip())
            self.refresh()
            self.collections_changed.emit()
        except Exception as exc:
            ErrorPresenter.error(self, str(exc))

    def _duplicate_request(self, request_id: int):
        try:
            self._collection_facade.duplicate_request(request_id)
            self.refresh()
            self.collections_changed.emit()
        except Exception as exc:
            ErrorPresenter.error(self, str(exc))

    # ── Delete ────────────────────────────────────────────────────────

    def _delete_collection(self, collection_id: int):
        reply = QMessageBox.question(
            self,
            "Confirm Delete",
            "Delete this collection and all its requests?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            try:
                self._collection_facade.delete_collection(collection_id)
                self.refresh()
                self.collections_changed.emit()
            except Exception as e:
                ErrorPresenter.error(self, f"Failed to delete collection: {e}")

    def _delete_request(self, request_id: int) -> None:
        reply = QMessageBox.question(
            self,
            "Confirm Delete",
            "Delete this request?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            try:
                self._collection_facade.delete_request(request_id)
                self.refresh()
                self.collections_changed.emit()
            except Exception as e:
                ErrorPresenter.error(self, f"Failed to delete request: {e}")

    def _manage_variables(self, collection_id: int):
        from equinox.gui.dialogs.collection_variables_dialog import CollectionVariablesDialog

        collection = self._collection_facade.get_collection(collection_id)
        if not collection:
            ErrorPresenter.error(self, "Collection not found")
            return

        dialog = CollectionVariablesDialog(self.db, collection_id, collection["name"], self)
        dialog.exec()

    # ── Folder helpers ────────────────────────────────────────────────

    @staticmethod
    def _col_id_for_item(item) -> "int | None": #  type: ignore[no-untyped-def]
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
        try:
            self._collection_facade.create_folder(col_id, path.strip())
            self.refresh()
            self.collections_changed.emit()
        except Exception as exc:
            ErrorPresenter.error(self, str(exc))

    def _create_subfolder(self, col_id: "int | None", parent_path: str) -> None:
        """Prompt the user for a subfolder name and create it under *parent_path*."""
        if col_id is None:
            return
        name, ok = QInputDialog.getText(
            self, "Add Subfolder", f'Subfolder name (inside "{parent_path}"):'
        )
        if not ok or not name.strip():
            return
        full_path = f"{parent_path}/{name.strip()}"
        try:
            self._collection_facade.create_folder(col_id, full_path)
            self.refresh()
            self.collections_changed.emit()
        except Exception as exc:
            ErrorPresenter.error(self, str(exc))

    # ── Request creation from the panel ──────────────────────────────

    def _new_request_in_collection(self, col_id: "int | None") -> None:
        """Create a new request at the collection root and open it in the editor."""
        if col_id is None:
            return
        from equinox.gui.collection_panel.panel import _NewRequestDialog

        dlg = _NewRequestDialog(self, title="New Request")
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        name, method, url = dlg.values()
        self._save_and_open_request(col_id, name, method, url, folder=None)

    def _new_request_in_folder(self, col_id: "int | None", folder_path: str) -> None:
        """Create a new request inside *folder_path* and open it in the editor."""
        if col_id is None:
            return
        from equinox.gui.collection_panel.panel import _NewRequestDialog

        dlg = _NewRequestDialog(
            self, title=f'New Request in "{folder_path}"', folder_hint=folder_path
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
        req = Request(
            method=method,
            url=url,
            name=name,
            collection_id=col_id,
            folder=folder,
        )
        try:
            req_id = self._collection_facade.save_request(req, collection_id=col_id, name=name)
            saved = self._collection_facade.get_request(req_id)
            if saved:
                self.request_selected.emit(saved)
            self.refresh()
            self.collections_changed.emit()
        except Exception as exc:
            ErrorPresenter.error(self, f"Failed to create request: {exc}")

    # ── Folder rename / delete ────────────────────────────────────────

    def _rename_folder(
        self,
        col_id: "int | None",
        old_path: str,
        item: Any,
    ) -> None:
        if col_id is None:
            return
        new_path, ok = QInputDialog.getText(
            self, "Rename Folder", "New folder name/path:", text=old_path
        )
        if not ok or not new_path.strip() or new_path.strip() == old_path:
            return
        try:
            self._collection_facade.rename_folder(col_id, old_path, new_path.strip())
            self.refresh()
            self.collections_changed.emit()
        except Exception as exc:
            ErrorPresenter.error(self, str(exc))

    def _delete_folder(self, col_id: "int | None", folder_path: str) -> None:
        if col_id is None:
            return
        reply = QMessageBox.question(
            self,
            "Delete Folder",
            f'Delete folder "{folder_path}"?\n\n'
            "Choose Yes to move requests to root, or No to delete them.",
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No
            | QMessageBox.StandardButton.Cancel,
        )
        if reply == QMessageBox.StandardButton.Cancel:
            return
        move_to_root = reply == QMessageBox.StandardButton.Yes
        try:
            self._collection_facade.delete_folder(col_id, folder_path, move_to_root=move_to_root)
            self.refresh()
            self.collections_changed.emit()
        except Exception as exc:
            ErrorPresenter.error(self, str(exc))

    def _move_to_folder(self, request_id: int) -> None:
        folder_path, ok = QInputDialog.getText(
            self,
            "Move to Folder",
            "Folder path (leave empty to move to root):",
        )
        if not ok:
            return
        try:
            self._collection_facade.move_request_to_folder(request_id, folder_path.strip() or None)
            self.refresh()
            self.collections_changed.emit()
        except Exception as exc:
            ErrorPresenter.error(self, str(exc))

    def _on_request_dropped(self, request_id: int, target_col_id: int, target_folder: str) -> None:
        """Handle a drag-and-drop move of a request to a new collection/folder."""
        try:
            # Check if it's a cross-collection or same-collection move
            req = self._collection_facade.get_request(request_id)
            if not req:
                return

            source_col = req.collection_id
            source_folder = req.folder

            # Nothing to do if destination is identical
            if source_col == target_col_id and (source_folder or None) == (target_folder or None):
                return

            if source_col != target_col_id:
                self._collection_facade.move_request_to_collection(
                    request_id, target_col_id, target_folder
                )
            else:
                self._collection_facade.move_request_to_folder(request_id, target_folder)

            self.refresh()
            self.collections_changed.emit()
        except Exception as exc:
            ErrorPresenter.error(self, f"Failed to move request: {exc}")

    def _on_request_reorder(self, dragged_id: int, target_id: int) -> None:
        """Handle reordering: place *dragged_id* immediately before *target_id*."""
        try:
            self._collection_facade.reorder_request_before_target(dragged_id, target_id)

            self.refresh()
            self.collections_changed.emit()
        except Exception as exc:
            ErrorPresenter.error(self, f"Failed to reorder: {exc}")

    def _sort_group(self, col_id: int, folder: "str | None", mode: str) -> None:
        """Sort requests in a collection/folder group."""
        try:
            if mode == "alpha":
                self._collection_facade.sort_requests_alphabetically(col_id, folder)
            elif mode == "method":
                self._collection_facade.sort_requests_by_method(col_id, folder)
            self.refresh()
            self.collections_changed.emit()
        except Exception as exc:
            ErrorPresenter.error(self, f"Failed to sort: {exc}")

    # ── Hierarchical auth ─────────────────────────────────────────────

    def _set_collection_auth(self, col_id: int) -> None:
        """Open the auth dialog and persist the result on the collection."""
        current_auth = self._collection_facade.get_collection_auth(col_id)
        from equinox.gui.dialogs.auth_dialog import AuthDialog

        dialog = AuthDialog(current_auth, self, db=self.db)
        if dialog.exec() == QDialog.DialogCode.Accepted and hasattr(dialog, "_saved_auth"):
            self._collection_facade.set_collection_auth(col_id, dialog._saved_auth)
            self.collections_changed.emit()

    def _clear_collection_auth(self, col_id: int) -> None:
        self._collection_facade.set_collection_auth(col_id, None)
        self.collections_changed.emit()

    def _set_folder_auth(self, col_id: int, folder_path: str) -> None:
        """Open the auth dialog and persist the result on the folder."""
        current_auth = self._collection_facade.get_folder_auth(col_id, folder_path)
        from equinox.gui.dialogs.auth_dialog import AuthDialog

        dialog = AuthDialog(current_auth, self, db=self.db)
        if dialog.exec() == QDialog.DialogCode.Accepted and hasattr(dialog, "_saved_auth"):
            self._collection_facade.set_folder_auth(col_id, folder_path, dialog._saved_auth)
            self.collections_changed.emit()

    def _clear_folder_auth(self, col_id: int, folder_path: str) -> None:
        self._collection_facade.set_folder_auth(col_id, folder_path, None)
        self.collections_changed.emit()
