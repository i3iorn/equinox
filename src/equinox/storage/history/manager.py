"""HistoryManager — public orchestrator for request/response history."""

from __future__ import annotations

import logging
from typing import Any, Optional, Tuple, cast

from equinox.core.exceptions import SecurityError, StorageError, ValidationError
from equinox.core.request import Request, Response
from equinox.security import redact_url
from equinox.storage.database import Database
from equinox.storage.utils import require_positive_int as _require_positive_int

from ._indexer import _HistoryIndexer
from ._searcher import _HistorySearcher
from ._serializer import _HistorySerializer

__all__ = ["HistoryManager"]

logger = logging.getLogger(__name__)


class HistoryManager:
    """Manage request/response history.

    Delegates serialization to :class:`_HistorySerializer`, index maintenance
    to :class:`_HistoryIndexer`, and search mechanics to
    :class:`_HistorySearcher`.  Only orchestration and the public API live here.
    """

    MAX_HISTORY_ENTRIES = 100_000
    DEFAULT_LIMIT = 100

    # Backward-compatible aliases — canonical values live on the helpers.
    MAX_BODY_SIZE = _HistorySerializer.MAX_BODY_SIZE
    MAX_HEADERS_SIZE = _HistorySerializer.MAX_HEADERS_SIZE
    MAX_URL_LENGTH = _HistorySerializer.MAX_URL_LENGTH
    MAX_ERROR_MESSAGE_LENGTH = _HistorySerializer.MAX_ERROR_MESSAGE_LENGTH
    MAX_REGEX_LENGTH = _HistorySearcher.MAX_REGEX_LENGTH
    MAX_LIMIT = _HistorySearcher.MAX_LIMIT

    def __init__(self, db: Database) -> None:
        self.db = db
        self._serializer = _HistorySerializer()
        self._indexer = _HistoryIndexer(db)
        self._searcher = _HistorySearcher(db, self._serializer)

    # ── Public write API ──────────────────────────────────────────────────────

    def save_history(
        self,
        request: Request,
        response: Optional[Response] = None,
        error: Optional[str] = None,
    ) -> int:
        """Save request/response to history and return the new history ID.

        Raises:
            ValidationError: If the request fields are invalid.
            SecurityError: If size limits are exceeded.
            StorageError: If the DB write fails.
        """
        safe_url = (redact_url(request.url) or "")[:60] if request.url else ""
        logger.debug("save_history() called for %s %s", request.method, safe_url)
        self._prune_oldest_entry_if_limit_reached()

        req = self._serializer.prepare_request(request)
        resp = self._serializer.prepare_response(response)
        error_str = self._serializer.truncate_error(error)

        try:
            history_id = self.db.insert(
                """
                INSERT INTO history
                (request_id, method, url, status_code, reason, request_headers,
                 request_body, response_headers, response_body, elapsed, error,
                 request_correlation_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    getattr(request, "id", None),
                    req["method"],
                    req["url"],
                    resp["status_code"],
                    resp["reason"],
                    req["headers_json"],
                    req["body"],
                    resp["headers_json"],
                    resp["body"],
                    resp["elapsed"],
                    error_str,
                    req["request_correlation_id"],
                ),
            )
        except (ValidationError, SecurityError, StorageError):
            raise
        except Exception as exc:
            logger.error("Failed to save history entry: %s", exc)
            raise StorageError(f"Failed to save history: {exc}") from exc

        logger.info(
            "Saved history entry id=%d: %s %s status=%s elapsed=%.2fs",
            history_id,
            req["method"],
            req["url"],
            resp["status_code"] or "error",
            resp["elapsed"] or 0,
        )
        self._indexer.index(history_id, req["method"], req["url"], resp["status_code"], response)
        return int(history_id)

    def delete_history(self, history_id: int) -> None:
        """Delete a history entry by ID.

        Raises:
            ValidationError: If *history_id* is invalid.
            StorageError: If the entry doesn't exist or deletion fails.
        """
        _require_positive_int(history_id, "History ID")
        try:
            cursor = self.db.execute("DELETE FROM history WHERE id = ?", (history_id,))
            if cursor.rowcount == 0:
                raise StorageError(f"History entry with ID {history_id} does not exist")
            logger.info("Deleted history entry %d", history_id)
        except StorageError:
            raise
        except Exception as exc:
            raise StorageError(f"Failed to delete history entry: {exc}")

    def clear_history(self, days: int | None = None) -> None:
        """Clear history, optionally retaining entries newer than *days* days.

        Raises:
            ValidationError: If *days* is invalid.
            StorageError: If deletion fails.
        """
        if days is not None:
            _require_positive_int(days, "Days")
            if days > 36500:
                raise ValidationError("Days value too large (max 36500)")
        try:
            with self.db.transaction() as tx:
                if days is not None:
                    cursor = tx.execute(
                        "DELETE FROM history"
                        " WHERE executed_at < datetime('now', '-' || ? || ' days')",
                        (days,),
                    )
                    logger.warning(
                        "Deleted %d history entries older than %d days",
                        cursor.rowcount,
                        days,
                    )
                else:
                    cursor = tx.execute("DELETE FROM history")
                    logger.warning("Cleared all %d history entries", cursor.rowcount)
        except StorageError:
            raise
        except Exception as exc:
            raise StorageError(f"Failed to clear history: {exc}") from exc

    # ── Public read API ───────────────────────────────────────────────────────

    def get_history(self, history_id: int) -> dict[str, Any] | None:
        """Return a single history entry by ID, or *None* if not found.

        Raises:
            ValidationError: If *history_id* is invalid.
        """
        _require_positive_int(history_id, "History ID")
        row = self.db.fetchone("SELECT * FROM history WHERE id = ?", (history_id,))
        if row is None:
            return None
        return self._serializer.decode_row(dict(row), row_id=history_id)

    def list_history(
        self,
        limit: int = 100,
        offset: int = 0,
        request_id: int | None = None,
    ) -> list[dict[str, Any]]:
        """List history entries, newest first.

        Args:
            limit:      Maximum rows to return.
            offset:     Number of rows to skip.
            request_id: Filter to this saved-request ID when provided.

        Raises:
            ValidationError: If pagination parameters are invalid.
        """
        self._searcher.validate_pagination(limit, offset)

        if request_id is not None:
            _require_positive_int(request_id, "Request ID")
            sql = (
                "SELECT * FROM history WHERE request_id = ? "
                "ORDER BY executed_at DESC LIMIT ? OFFSET ?"
            )
            params: Tuple[Any, ...] = (request_id, limit, offset)
        else:
            sql = "SELECT * FROM history ORDER BY executed_at DESC LIMIT ? OFFSET ?"
            params = (limit, offset)

        rows = self.db.fetchall(sql, params)
        return [self._serializer.decode_row(dict(row), row_id=row["id"]) for row in rows]

    def search_history(
        self,
        query: str = "",
        method: str = "",
        status_class: str = "",
        status_code: int | None = None,
        body_regex: str = "",
        jsonpath: str = "",
        jsonpath_value: str | None = None,
        content_type: str = "",
        header: str = "",
        min_elapsed: float | None = None,
        max_elapsed: float | None = None,
        executed_after: str | None = None,
        executed_before: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Filter history with SQL WHERE clauses and Python post-filters.

        **SQL-level filters** (fast, use indexes):

        * *query* — text to search in URL or request body (case-insensitive LIKE).
        * *method* — HTTP method (e.g. ``"GET"``).  Empty string = all.
        * *status_class* — ``"2xx"``, ``"3xx"``, ``"4xx"``, ``"5xx"``,
          ``"errors"`` or ``""`` (all).
        * *status_code* — exact integer status code.  Takes precedence over
          *status_class* when both are given.
        * *content_type* — substring match against ``response_headers``.
        * *min_elapsed* / *max_elapsed* — response-time bounds in seconds.
        * *executed_after* / *executed_before* — ISO-8601 timestamp strings.

        **Python post-filters** (applied after SQL fetch):

        * *body_regex* — Python regex tested against the response body
          (case-insensitive, max 500 chars).
        * *jsonpath* — JSONPath expression (``jsonpath-ng`` syntax) that must
          match at least one node in the parsed JSON response body.
        * *jsonpath_value* — if given with *jsonpath*, the first match must
          equal this string.
        * *header* — ``"Name: value"`` substring match in response headers.

        Raises:
            ValidationError: On invalid limit/offset, bad regex, or bad JSONPath.
        """
        return cast(
            list[dict[str, Any]],
            self._searcher.search(
            query=query,
            method=method,
            status_class=status_class,
            status_code=status_code,
            body_regex=body_regex,
            jsonpath=jsonpath,
            jsonpath_value=jsonpath_value,
            content_type=content_type,
            header=header,
            min_elapsed=min_elapsed,
            max_elapsed=max_elapsed,
            executed_after=executed_after,
            executed_before=executed_before,
            limit=limit,
            offset=offset,
            ),
        )

    def get_stats(self) -> dict[str, Any]:
        """Return aggregate history statistics in a single DB round-trip."""
        row = self.db.fetchone(
            "SELECT"
            "  COUNT(*)                                             AS total,"
            "  SUM(CASE WHEN status_code IS NOT NULL"
            "           AND status_code < 400 THEN 1 ELSE 0 END)  AS successful,"
            "  SUM(CASE WHEN status_code >= 400"
            "           OR error IS NOT NULL  THEN 1 ELSE 0 END)  AS failed"
            " FROM history"
        )
        row = row or {}
        return {
            "total": row.get("total") or 0,
            "successful": row.get("successful") or 0,
            "failed": row.get("failed") or 0,
        }

    # ── Private helpers ───────────────────────────────────────────────────────

    def _prune_oldest_entry_if_limit_reached(self) -> None:
        """Delete the oldest ~1 % of entries when the history cap is reached."""
        count_row = self.db.fetchone("SELECT COUNT(*) as count FROM history")
        if count_row and count_row["count"] >= self.MAX_HISTORY_ENTRIES:
            prune_count = max(1, self.MAX_HISTORY_ENTRIES // 100)
            logger.warning(
                "History limit reached (%d), removing %d oldest entries",
                self.MAX_HISTORY_ENTRIES,
                prune_count,
            )
            self.db.execute(
                "DELETE FROM history WHERE id IN"
                " (SELECT id FROM history ORDER BY executed_at ASC LIMIT ?)",
                (prune_count,),
            )
