"""History loading and response reconstruction mixin for MainWindow."""

# mypy: disable-error-code=attr-defined

from __future__ import annotations

import logging

from equinox.application.history import HistoryFacade
from equinox.core.request import Request
from equinox.gui.error_presenter import ErrorPresenter

logger = logging.getLogger(__name__)


class _HistoryMixin:
    """Methods for loading and replaying requests from history."""

    @staticmethod
    def _request_from_history(entry: dict) -> Request:
        """Backward-compatible wrapper for tests and legacy call sites."""
        return HistoryFacade.request_from_entry(entry)

    def _fetch_history_entry(self, history_id: int) -> dict | None:
        """Fetch a history entry by ID, or None."""
        return self._history_facade.get_history(history_id)

    def _fetch_and_load_history(self, history_id: int) -> tuple[dict, Request] | None:
        """Autosave, fetch, build, and load a history entry into the request panel.

        Returns ``(entry, request)`` on success, ``None`` when the entry is absent.
        """
        self.request_panel.autosave_current()
        entry = self._fetch_history_entry(history_id)
        if not entry:
            logger.debug("_fetch_and_load_history: no entry for id=%s", history_id)
            return None
        request = self._history_facade.request_from_entry(entry)
        try:
            self.request_panel.load_request(request)
        except Exception:
            logger.error("Failed to load request from history id=%s", history_id, exc_info=True)
        return entry, request

    # ── Signal handlers ───────────────────────────────────────────────────────

    def _load_history_entry(self, history_id: int) -> None:
        """Load and display a history entry in the request/response panels."""
        try:
            result = self._fetch_and_load_history(history_id)
            if result is None:
                return
            entry, request = result
            response = self._history_facade.response_from_entry(entry, request, history_id)
            if response is not None:
                self.response_panel.display_response(response)
                self._run_intelligence_analysis(response)
            else:
                self.response_panel.intelligence_panel.clear()
                self.response_panel.set_intelligence_badge(0)
        except Exception:
            logger.error("Unhandled error loading history entry id=%s", history_id, exc_info=True)
            try:
                ErrorPresenter.error(
                    self,
                    f"Failed to load history entry {history_id}. See log for details.",
                )
            except Exception:
                logger.debug("Also failed to show error dialog for history load", exc_info=True)

    def _replay_history_entry(self, history_id: int) -> None:
        """Re-run a history entry exactly as originally sent."""
        if self._fetch_and_load_history(history_id) is None:
            return
        from PyQt6.QtCore import QTimer

        QTimer.singleShot(0, self.request_panel.send)
