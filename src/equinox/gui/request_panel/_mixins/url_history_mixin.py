"""URL history completer helpers for ``RequestPanel``."""

from __future__ import annotations

import logging
import time
from typing import Any
from typing import cast

from equinox.gui.request_panel._constants import COMPLETER_MAX_VISIBLE
from equinox.gui.request_panel._constants import HISTORY_COMPLETER_LIMIT
from PyQt6.QtCore import QObject
from PyQt6.QtCore import QStringListModel
from PyQt6.QtCore import Qt
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QCompleter
from PyQt6.QtWidgets import QWidget

logger = logging.getLogger(__name__)


class URLHistoryMixin:
    """Manage the URL auto-completer backed by recent request history."""

    _request_history: Any
    url_input: Any
    _url_model: QStringListModel
    _known_urls: set[str]
    _url_values: list[str]

    def _setup_url_completer(self) -> None:
        """Configure the URL completer and defer the initial history fetch."""
        host = cast(QWidget, cast(object, self))
        self._url_model = QStringListModel(cast(QObject, host))
        self._known_urls: set[str] = set()
        self._url_values: list[str] = []
        completer = QCompleter(self._url_model, cast(QObject, host))
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        completer.setMaxVisibleItems(COMPLETER_MAX_VISIBLE)
        self.url_input.setCompleter(completer)
        QTimer.singleShot(0, self._refresh_url_completer)

    def _refresh_url_completer(self) -> None:
        """Populate the completer model from recent history URLs."""
        started_at = time.perf_counter()
        try:
            recent_urls = self._request_history.list_recent_urls(limit=HISTORY_COMPLETER_LIMIT)
            self._url_values = list(recent_urls)
            self._known_urls = set(recent_urls)
            self._url_model.setStringList(recent_urls)
            elapsed_ms = int((time.perf_counter() - started_at) * 1000)
            logger.debug(
                "request_panel.url_completer_refreshed entries=%d elapsed_ms=%d",
                len(recent_urls),
                elapsed_ms,
            )
        except Exception:
            logger.exception("Failed to refresh URL completer", exc_info=True)

    def _add_url_to_completer(self, url: str) -> None:
        """Add a URL to the in-memory completer list without a re-query."""
        cleaned = (url or "").strip()
        if not cleaned or cleaned in self._known_urls:
            return
        self._known_urls.add(cleaned)
        self._url_values.insert(0, cleaned)
        if len(self._url_values) > HISTORY_COMPLETER_LIMIT:
            dropped = self._url_values.pop()
            self._known_urls.discard(dropped)
        self._url_model.setStringList(self._url_values)
