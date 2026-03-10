"""Folder management methods for CollectionManager."""

import logging
from typing import List

from equinox.core.exceptions import StorageError, ValidationError
from equinox.storage.utils import require_positive_int

logger = logging.getLogger(__name__)

MAX_FOLDER_PATH_LENGTH = 1000


def _validate_folder_path(path: str, label: str = "Folder") -> str:
    """Validate and strip a folder path.  Returns the stripped path.

    Raises:
        ValidationError: If the path is empty, malformed, or contains
            dangerous characters.
    """
    path = (path or "").strip()
    if not path:
        raise ValidationError(f"{label} path must not be empty")
    if path.startswith("/") or path.endswith("/"):
        raise ValidationError(f"{label} path must not start or end with '/'")
    if "//" in path:
        raise ValidationError(f"{label} path must not contain empty segments ('//')")
    if ".." in path.split("/"):
        raise ValidationError(f"{label} path must not contain '..' segments")
    if "\r" in path or "\n" in path:
        raise ValidationError(f"{label} path contains invalid characters")
    if len(path) > MAX_FOLDER_PATH_LENGTH:
        raise ValidationError(
            f"{label} path too long (max {MAX_FOLDER_PATH_LENGTH} characters)"
        )
    return path


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
        path = _validate_folder_path(path)
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
        old_path = _validate_folder_path(old_path, "Old folder")
        new_path = _validate_folder_path(new_path, "New folder")

        # Batch-update request folders: replace old_path prefix with new_path.
        # CASE handles both exact match and sub-folder prefix in one statement.
        self.db.execute(
            "UPDATE requests SET "
            "folder = CASE "
            "  WHEN folder = ? THEN ? "
            "  ELSE ? || SUBSTR(folder, ?) "
            "END, "
            "updated_at = CURRENT_TIMESTAMP "
            "WHERE collection_id = ? AND (folder = ? OR folder LIKE ?)",
            (
                old_path, new_path,                       # exact match
                new_path, len(old_path) + 1,              # prefix replacement
                collection_id, old_path, f"{old_path}/%", # WHERE
            ),
        )

        # Batch-update collection_folders records in sync.
        self.db.execute(
            "UPDATE collection_folders SET "
            "path = CASE "
            "  WHEN path = ? THEN ? "
            "  ELSE ? || SUBSTR(path, ?) "
            "END "
            "WHERE collection_id = ? AND (path = ? OR path LIKE ?)",
            (
                old_path, new_path,
                new_path, len(old_path) + 1,
                collection_id, old_path, f"{old_path}/%",
            ),
        )

        logger.info(
            "Renamed folder %r → %r in collection %d",
            old_path, new_path, collection_id,
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

        Raises:
            ValidationError: If *collection_id* or *folder_path* is invalid.
        """
        require_positive_int(collection_id, "Collection ID")
        folder_path = _validate_folder_path(folder_path)
        # Count affected requests for logging
        count_row = self.db.fetchone(
            "SELECT COUNT(*) AS cnt FROM requests WHERE collection_id=? AND "
            "(folder=? OR folder LIKE ?)",
            (collection_id, folder_path, f"{folder_path}/%"),
        )
        request_count = (count_row or {}).get("cnt", 0)
        if request_count:
            if move_to_root:
                self.db.execute(
                    "UPDATE requests SET folder=NULL, updated_at=CURRENT_TIMESTAMP "
                    "WHERE collection_id=? AND (folder=? OR folder LIKE ?)",
                    (collection_id, folder_path, f"{folder_path}/%"),
                )
                logger.info(
                    "Moved %d request(s) from folder %r to root in collection %d",
                    request_count, folder_path, collection_id,
                )
            else:
                self.db.execute(
                    "DELETE FROM requests WHERE collection_id=? AND "
                    "(folder=? OR folder LIKE ?)",
                    (collection_id, folder_path, f"{folder_path}/%"),
                )
                logger.info(
                    "Deleted %d request(s) in folder %r from collection %d",
                    request_count, folder_path, collection_id,
                )
        # Always clean up explicit folder records (handles empty folders too)
        self.db.execute(
            "DELETE FROM collection_folders WHERE collection_id=? AND (path=? OR path LIKE ?)",
            (collection_id, folder_path, f"{folder_path}/%"),
        )
