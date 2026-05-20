"""Request history boundary for request-panel flows.

This module provides a small application-layer service for request-panel
history interactions so GUI code does not construct ``HistoryManager``
directly. It currently covers URL-completer lookups and deferred request
history persistence.
"""

from __future__ import annotations

import logging

from equinox.core.request import Request, Response
from equinox.storage import Database, HistoryManager

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

    def save_history_safe(
        self,
        request: Request,
        response: Response | None = None,
        error: str | None = None,
    ) -> None:
        """Persist history without letting storage errors bubble into the GUI."""
        if request is None:
            return
        try:
            if response is not None:
                self._history_manager.save_history(request, response)
            elif error is not None:
                self._history_manager.save_history(request, error=error)
        except Exception:
            logger.debug("Failed to save history", exc_info=True)
