"""Request history management"""

import json
from typing import List, Dict, Any, Optional

from equinox.storage.database import Database
from equinox.core.request import Request, Response
from equinox.core.exceptions import StorageError


class HistoryManager:
    """Manage request/response history"""

    def __init__(self, db: Database):
        """
        Initialize history manager

        Args:
            db: Database instance
        """
        self.db = db

    def save_history(
        self, request: Request, response: Optional[Response] = None, error: Optional[str] = None
    ) -> int:
        """
        Save request/response to history

        Args:
            request: Request object
            response: Response object (if successful)
            error: Error message (if failed)

        Returns:
            History ID
        """
        try:
            return self.db.insert(
                """
                INSERT INTO history
                (request_id, method, url, status_code, reason, request_headers, request_body,
                 response_headers, response_body, elapsed, error)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    getattr(request, "id", None),
                    request.method,
                    request.url,
                    response.status_code if response else None,
                    response.reason if response else None,
                    json.dumps(request.headers),
                    request.body,
                    json.dumps(dict(response.headers)) if response else None,
                    response.body if response else None,
                    response.elapsed if response else None,
                    error,
                ),
            )
        except Exception as e:
            raise StorageError(f"Failed to save history: {e}")

    def get_history(self, history_id: int) -> Optional[Dict[str, Any]]:
        """
        Get history entry by ID

        Args:
            history_id: History ID

        Returns:
            History data or None
        """
        row = self.db.fetchone("SELECT * FROM history WHERE id = ?", (history_id,))
        if row:
            row = dict(row)
            row["request_headers"] = json.loads(row["request_headers"])
            if row["response_headers"]:
                row["response_headers"] = json.loads(row["response_headers"])
        return row

    def list_history(
        self, limit: int = 100, offset: int = 0, request_id: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        List history entries

        Args:
            limit: Maximum number of entries to return
            offset: Number of entries to skip
            request_id: Optional request ID to filter by

        Returns:
            List of history entries
        """
        if request_id:
            query = "SELECT * FROM history WHERE request_id = ? ORDER BY executed_at DESC LIMIT ? OFFSET ?"
            params = (request_id, limit, offset)
        else:
            query = "SELECT * FROM history ORDER BY executed_at DESC LIMIT ? OFFSET ?"
            params = (limit, offset)

        rows = self.db.fetchall(query, params)
        for row in rows:
            row["request_headers"] = json.loads(row["request_headers"])
            if row["response_headers"]:
                row["response_headers"] = json.loads(row["response_headers"])
        return rows

    def delete_history(self, history_id: int) -> None:
        """Delete history entry"""
        self.db.execute("DELETE FROM history WHERE id = ?", (history_id,))

    def clear_history(self, days: Optional[int] = None) -> None:
        """
        Clear history

        Args:
            days: If specified, only delete history older than this many days
        """
        if days:
            self.db.execute(
                "DELETE FROM history WHERE executed_at < datetime('now', '-' || ? || ' days')",
                (days,),
            )
        else:
            self.db.execute("DELETE FROM history")

    def get_stats(self) -> Dict[str, Any]:
        """Get history statistics"""
        total = self.db.fetchone("SELECT COUNT(*) as count FROM history")
        successful = self.db.fetchone(
            "SELECT COUNT(*) as count FROM history WHERE status_code IS NOT NULL AND status_code < 400"
        )
        failed = self.db.fetchone(
            "SELECT COUNT(*) as count FROM history WHERE status_code >= 400 OR error IS NOT NULL"
        )

        return {
            "total": total["count"] if total else 0,
            "successful": successful["count"] if successful else 0,
            "failed": failed["count"] if failed else 0,
        }
