"""Request history boundary for request-panel and response-panel flows.

This module provides a small application-layer service for history
interactions so GUI code does not construct ``HistoryManager`` directly. It
covers URL-completer lookups, deferred request history persistence, and
request/method-scoped history search (used by the response panel's "Diff
vs. History" feature).
"""

from __future__ import annotations

import logging
from typing import Any

from equinox.core.request import Request
from equinox.core.request import Response
from equinox.storage import Database
from equinox.storage import HistoryManager

logger = logging.getLogger(__name__)


class RequestHistoryService:
    """Small boundary around request-panel history reads and writes."""

    def __init__(
        self,
        db: Database,
        history_manager: HistoryManager | None = None,
    ) -> None:
        self._history_manager = history_manager or HistoryManager(db)

    def list_recent_urls(self, *, limit: int) -> list[str]:
        """Return recent unique history URLs in most-recent-first order."""
        entries = self._history_manager.list_history(limit=limit)
        urls = [entry["url"] for entry in entries if entry.get("url")]
        return list(dict.fromkeys(urls))

    def search_recent(self, *, query: str, method: str, limit: int) -> list[dict[str, Any]]:
        """Return history entries matching *query*/*method*, or [] on failure.

        Used by the response panel's "Diff vs. History" picker to find past
        runs of the same request without the GUI touching ``HistoryManager``
        directly.
        """
        try:
            return self._history_manager.search_history(query=query, method=method, limit=limit)
        except Exception:
            logger.exception("Failed to search history")
            return []

    def save_history_safe(
        self,
        request: Request,
        response: Response | None = None,
        error: str | None = None,
    ) -> None:
        """Persist history without letting storage errors bubble into the GUI."""
        try:
            if response is not None:
                self._history_manager.save_history(request, response)
            elif error is not None:
                self._history_manager.save_history(request, error=error)
        except Exception:
            logger.exception("Failed to save history", exc_info=True)
