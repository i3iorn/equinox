"""Folder management methods for CollectionManager."""

import logging
from typing import List

from equinox.core.exceptions import StorageError, ValidationError
from equinox.storage.utils import require_positive_int

logger = logging.getLogger(__name__)


class CollectionFoldersMixin:
    """Mixin providing folder management for CollectionManager."""

    def create_folder(
        self,
        collection_id: int,
        path: str,
        description: str = "",
    ) -> None:
        """Create an explicit folder record so the folder persists even when empty.

        Idempotent — silently succeeds if the folder already exists (INSERT OR IGNORE).

        Args:
            collection_id: Collection to create the folder in.
            path: Folder path (e.g. ``"Auth"`` or ``"Auth/OAuth"``).
                  Must be non-empty, must not start or end with ``/``,
                  and must not contain empty segments (``//``).
            description: Optional human description.

        Raises:
            ValidationError: If *collection_id* or *path* is invalid.
            StorageError: If the collection does not exist or the DB write fails.
        """
        require_positive_int(collection_id, "Collection ID")
        path = (path or "").strip()
        if not path:
            raise ValidationError("Folder path must not be empty")
        if path.startswith("/") or path.endswith("/"):
            raise ValidationError("Folder path must not start or end with '/'")
        if "//" in path:
            raise ValidationError("Folder path must not contain empty segments ('//')")
        if not self.get_collection(collection_id):
            raise StorageError(f"Collection {collection_id} does not exist")
        try:
            self.db.execute(
                "INSERT OR IGNORE INTO collection_folders "
                "(collection_id, path, description) VALUES (?, ?, ?)",
                (collection_id, path, description or ""),
            )
            logger.info("Created folder %r in collection %d", path, collection_id)
        except Exception as exc:
            raise StorageError(f"Failed to create folder: {exc}") from exc

    def list_folders(self, collection_id: int) -> List[str]:
        """Return all explicit folder paths for *collection_id*, sorted alphabetically."""
        rows = self.db.fetchall(
            "SELECT path FROM collection_folders WHERE collection_id=? ORDER BY path",
            (collection_id,),
        )
        return [row["path"] for row in rows]

    def rename_folder(self, collection_id: int, old_path: str, new_path: str) -> None:
        """Rename a folder and all sub-folders within a collection.

        All requests whose folder equals *old_path* or starts with
        ``old_path/`` have the leading prefix replaced by *new_path*.

        Args:
            collection_id: Collection to operate on.
            old_path: Current folder path (e.g. "Auth").
            new_path: New folder path (e.g. "Authentication").

        Raises:
            ValidationError: If paths are invalid.
        """
        require_positive_int(collection_id, "Collection ID")
        old_path = (old_path or "").strip()
        new_path = (new_path or "").strip()
        if not old_path:
            raise ValidationError("Old folder path must not be empty")
        if not new_path:
            raise ValidationError("New folder path must not be empty")
        for label, path in [("Old", old_path), ("New", new_path)]:
            if path.startswith("/") or path.endswith("/"):
                raise ValidationError(f"{label} folder path must not start or end with '/'")
            if "//" in path:
                raise ValidationError(f"{label} folder path must not contain empty segments ('//')")
            if ".." in path.split("/"):
                raise ValidationError(f"{label} folder path must not contain '..' segments")
            if "\r" in path or "\n" in path:
                raise ValidationError(f"{label} folder path contains invalid characters")
            if len(path) > 1000:
                raise ValidationError(f"{label} folder path too long (max 1000 characters)")
        rows = self.db.fetchall(
            "SELECT id, folder FROM requests WHERE collection_id=? AND ("
            "folder=? OR folder LIKE ?)",
            (collection_id, old_path, f"{old_path}/%"),
        )
        for row in rows:
            current = row["folder"] or ""
            if current == old_path:
                updated = new_path
            elif current.startswith(f"{old_path}/"):
                updated = new_path + current[len(old_path):]
            else:
                continue
            self.db.execute(
                "UPDATE requests SET folder=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (updated or None, row["id"]),
            )
        # Keep collection_folders records in sync
        folder_rows = self.db.fetchall(
            "SELECT id, path FROM collection_folders "
            "WHERE collection_id=? AND (path=? OR path LIKE ?)",
            (collection_id, old_path, f"{old_path}/%"),
        )
        for row in folder_rows:
            current = row["path"]
            if current == old_path:
                updated = new_path
            elif current.startswith(f"{old_path}/"):
                updated = new_path + current[len(old_path):]
            else:
                continue
            self.db.execute(
                "UPDATE collection_folders SET path=? WHERE id=?",
                (updated, row["id"]),
            )
        logger.info(
            "Renamed folder %r → %r in collection %d (%d request(s), %d folder record(s) updated)",
            old_path, new_path, collection_id, len(rows), len(folder_rows),
        )

    def delete_folder(
        self,
        collection_id: int,
        folder_path: str,
        move_to_root: bool = True,
    ) -> None:
        """Delete a folder within a collection.

        Args:
            collection_id: Collection to operate on.
            folder_path: Folder path to delete (e.g. "Auth/OAuth").
            move_to_root: If True (default), move all requests in the folder
                (and sub-folders) to the collection root.  If False, delete
                those requests entirely.
        """
        rows = self.db.fetchall(
            "SELECT id FROM requests WHERE collection_id=? AND ("
            "folder=? OR folder LIKE ?)",
            (collection_id, folder_path, f"{folder_path}/%"),
        )
        request_ids = [row["id"] for row in rows]
        if request_ids:
            if move_to_root:
                for req_id in request_ids:
                    self.db.execute(
                        "UPDATE requests SET folder=NULL, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                        (req_id,),
                    )
                logger.info(
                    "Moved %d request(s) from folder %r to root in collection %d",
                    len(request_ids), folder_path, collection_id,
                )
            else:
                for req_id in request_ids:
                    self.db.execute("DELETE FROM requests WHERE id=?", (req_id,))
                logger.info(
                    "Deleted %d request(s) in folder %r from collection %d",
                    len(request_ids), folder_path, collection_id,
                )
        # Always clean up explicit folder records (handles empty folders too)
        self.db.execute(
            "DELETE FROM collection_folders WHERE collection_id=? AND (path=? OR path LIKE ?)",
            (collection_id, folder_path, f"{folder_path}/%"),
        )
