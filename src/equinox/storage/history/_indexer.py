"""Best-effort maintenance of the ``history_index`` fast-lookup table."""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime as _dt
from datetime import timezone as _tz
from typing import Any

from equinox.core import urls
from equinox.storage.database import Database
from equinox.storage.utils import safe_json_dumps

__all__ = ["_HistoryIndexer"]

logger = logging.getLogger(__name__)


class _HistoryIndexer:
    """Maintain the ``history_index`` fast-lookup table.

    All errors are caught and logged so indexing never blocks a history write.
    """

    MAX_PATH_SEGMENTS = 64
    MAX_QUERY_PARAMS = 128

    def __init__(self, db: Database) -> None:
        self._db = db

    # ── Public interface ──────────────────────────────────────────────────────

    def index(
        self,
        history_id: int,
        method: str,
        url: str,
        status_code: int | None,
        response_obj: Any,
    ) -> None:
        """Insert a normalized index row for *history_id* (best-effort)."""
        try:
            self._do_index(history_id, method, url, status_code, response_obj)
        except Exception as exc:
            logger.debug("Indexing history row %d failed: %s", history_id, exc)

    # ── Private helpers ───────────────────────────────────────────────────────

    def _do_index(
        self,
        history_id: int,
        method: str,
        url: str,
        status_code: int | None,
        response_obj: Any,
    ) -> None:
        expanded_url = urls.expand_placeholders(url, None)
        parts = urls.normalized_parts(expanded_url)
        normalized_url = parts.get("normalized_url")
        path_segments = parts.get("path_segments") or []
        query_params = parts.get("query_params") or {}

        if len(path_segments) > self.MAX_PATH_SEGMENTS:
            path_segments = path_segments[: self.MAX_PATH_SEGMENTS]
        if isinstance(query_params, dict) and len(query_params) > self.MAX_QUERY_PARAMS:
            query_params = dict(list(query_params.items())[: self.MAX_QUERY_PARAMS])

        response_success = 1 if isinstance(status_code, int) and 200 <= status_code < 300 else 0
        executed_at = _dt.now(_tz.utc).strftime("%Y-%m-%d %H:%M:%S")

        self._db.insert(
            """
            INSERT INTO history_index
            (history_id, method, normalized_url, path_segments, query_params,
             body_hash, response_success, executed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                history_id,
                method,
                normalized_url,
                safe_json_dumps(path_segments, max_len=4096),
                safe_json_dumps(query_params, max_len=8192),
                self._compute_body_hash(response_obj),
                response_success,
                executed_at,
            ),
        )

    @staticmethod
    def _compute_body_hash(response_obj: Any) -> str | None:
        """Return a SHA-256 hex digest of the response body, or ``None``."""
        if response_obj is None:
            return None
        try:
            if hasattr(response_obj, "body") and isinstance(response_obj.body, (bytes, bytearray)):
                raw = bytes(response_obj.body)
            elif hasattr(response_obj, "content") and isinstance(
                response_obj.content, (bytes, bytearray)
            ):
                raw = bytes(response_obj.content)
            else:
                raw = str(response_obj).encode("utf-8")
        except Exception:
            return None
        return hashlib.sha256(raw).hexdigest()
