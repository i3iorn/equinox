"""Request ordering and move methods for CollectionManager."""

import logging
from typing import Dict, List, Any, Optional

from equinox.core.exceptions import StorageError

logger = logging.getLogger(__name__)


class CollectionOrderingMixin:
    """Mixin providing request ordering and move for CollectionManager."""

    def move_request_to_folder(self, request_id: int, folder: Optional[str]) -> None:
        """Move a request to a different folder (or root if folder is None/empty).

        Args:
            request_id: Request ID to move.
            folder: Destination folder path (e.g. "Auth/OAuth"), or None/empty for root.
        """
        self.db.execute(
            "UPDATE requests SET folder=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (folder or None, request_id),
        )
        logger.info("Moved request %d to folder %r", request_id, folder)

    def move_request_to_collection(
        self,
        request_id: int,
        target_collection_id: int,
        folder: Optional[str] = None,
    ) -> None:
        """Move a request to a different collection (and optionally folder).

        Args:
            request_id: Request ID to move.
            target_collection_id: Destination collection ID.
            folder: Destination folder path, or ``None`` for collection root.

        Raises:
            StorageError: If the request or target collection does not exist.
        """
        if not self.db.fetchone("SELECT id FROM requests WHERE id = ?", (request_id,)):
            raise StorageError(f"Request {request_id} not found")
        if not self.get_collection(target_collection_id):
            raise StorageError(f"Target collection {target_collection_id} does not exist")
        self.db.execute(
            "UPDATE requests SET collection_id=?, folder=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (target_collection_id, folder or None, request_id),
        )
        logger.info(
            "Moved request %d to collection %d, folder %r",
            request_id, target_collection_id, folder,
        )

    # ── Reordering helpers ────────────────────────────────────────────

    def set_sort_order(self, request_id: int, sort_order: int) -> None:
        """Set the sort_order for a single request."""
        self.db.execute(
            "UPDATE requests SET sort_order=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (sort_order, request_id),
        )

    def reorder_requests(self, ordered_ids: List[int]) -> None:
        """Bulk-update sort_order for a list of request IDs.

        The first ID gets sort_order=0, the second gets 1, etc.

        Args:
            ordered_ids: Request IDs in the desired display order.
        """
        for position, req_id in enumerate(ordered_ids):
            self.db.execute(
                "UPDATE requests SET sort_order=? WHERE id=?",
                (position, req_id),
            )
        logger.info("Reordered %d requests", len(ordered_ids))

    def sort_requests_alphabetically(
        self, collection_id: int, folder: Optional[str] = None,
    ) -> None:
        """Sort requests A→Z by name within a collection+folder group.

        Args:
            collection_id: Collection to sort inside.
            folder: Folder path, or ``None`` for collection root.
        """
        rows = self._select_group(collection_id, folder)
        sorted_rows = sorted(rows, key=lambda r: (r["name"] or "").lower())
        for position, row in enumerate(sorted_rows):
            self.db.execute(
                "UPDATE requests SET sort_order=? WHERE id=?",
                (position, row["id"]),
            )
        logger.info(
            "Sorted %d request(s) alphabetically in collection %d, folder %r",
            len(sorted_rows), collection_id, folder,
        )

    def sort_requests_by_method(
        self, collection_id: int, folder: Optional[str] = None,
    ) -> None:
        """Sort requests by HTTP method then name within a collection+folder group.

        Method order: GET, POST, PUT, PATCH, DELETE, HEAD, OPTIONS, then others.

        Args:
            collection_id: Collection to sort inside.
            folder: Folder path, or ``None`` for collection root.
        """
        method_rank = {
            "GET": 0, "POST": 1, "PUT": 2, "PATCH": 3,
            "DELETE": 4, "HEAD": 5, "OPTIONS": 6,
        }
        rows = self._select_group(collection_id, folder)
        sorted_rows = sorted(
            rows,
            key=lambda r: (
                method_rank.get(r["method"], 99),
                (r["name"] or "").lower(),
            ),
        )
        for position, row in enumerate(sorted_rows):
            self.db.execute(
                "UPDATE requests SET sort_order=? WHERE id=?",
                (position, row["id"]),
            )
        logger.info(
            "Sorted %d request(s) by method in collection %d, folder %r",
            len(sorted_rows), collection_id, folder,
        )

    def _select_group(
        self, collection_id: int, folder: Optional[str],
    ) -> List[Dict[str, Any]]:
        """Return requests in a collection+folder group."""
        if folder:
            return self.db.fetchall(
                "SELECT id, name, method FROM requests "
                "WHERE collection_id=? AND folder=? ORDER BY sort_order, name",
                (collection_id, folder),
            )
        return self.db.fetchall(
            "SELECT id, name, method FROM requests "
            "WHERE collection_id=? AND (folder IS NULL OR folder='') "
            "ORDER BY sort_order, name",
            (collection_id,),
        )
