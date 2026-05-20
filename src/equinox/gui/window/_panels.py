"""Lazy left-panel initialization mixin for MainWindow.

Each left-tab panel is created on first selection and replaces its
placeholder widget, keeping startup cost near zero.
"""

# mypy: disable-error-code=attr-defined

from __future__ import annotations

import logging
from typing import Callable

logger = logging.getLogger(__name__)


class _PanelsMixin:
    """Lazy left-panel creation and active-tab refresh logic."""

    def _ensure_tab_initialized(self, index: int) -> None:
        """Create the real panel for *index* on first selection; swap the placeholder."""
        if index in self._tabs_initialized:
            return
        factories = {
            0: self._init_collections_panel,
            1: self._init_history_panel,
            2: self._init_variables_panel,
            3: self._init_logging_panel,
            4: self._init_cookies_panel,
            5: self._init_websocket_panel,
        }
        factory: Callable = factories.get(index)
        if factory is None:
            return
        try:
            panel = factory()
        except Exception:
            logger.exception(
                "Failed to initialize left panel index=%d (%s)",
                index,
                self._left_tabs.tabText(index),
            )
            return
        self._tabs_initialized.add(index)
        label = self._left_tabs.tabText(index)
        self._left_tabs.blockSignals(True)
        self._left_tabs.removeTab(index)
        self._left_tabs.insertTab(index, panel, label)
        self._left_tabs.setCurrentIndex(index)
        self._left_tabs.blockSignals(False)
        self._flush_pending_panel_refresh(index)
        logger.debug("Lazy-initialized left panel index=%d (%s)", index, label)

    def _init_collections_panel(self):
        from equinox.gui.collection_panel import CollectionsPanel

        self.collections_panel = CollectionsPanel(self.db, self)
        rp = self.request_panel
        self.collections_panel.request_selected.connect(self._load_request_guarded)
        self.collections_panel.request_run.connect(self._run_request_directly)
        self.collections_panel.collections_changed.connect(lambda: self.collections_panel.refresh())
        self.collections_panel.collections_changed.connect(rp.refresh_inherited_auth)
        return self.collections_panel

    def _init_history_panel(self):
        from equinox.gui.history_panel import HistoryPanel

        self.history_panel = HistoryPanel(self.db, self, history_facade=self._history_facade)
        self.history_panel.history_selected.connect(self._load_history_entry)
        self.history_panel.history_replay.connect(self._replay_history_entry)
        return self.history_panel

    def _init_variables_panel(self):
        from equinox.gui.variables_panel import VariablesPanel

        self.variables_panel = VariablesPanel(self.db, self)
        rp = self.request_panel
        rp.session_vars_changed.connect(self.variables_panel.refresh_session_vars)
        self.variables_panel.clear_session_requested.connect(rp.clear_session_vars)
        return self.variables_panel

    def _init_logging_panel(self):
        from equinox.gui.logging_panel import LoggingPanel

        self.logging_panel = LoggingPanel(self)
        return self.logging_panel

    def _init_cookies_panel(self):
        from equinox.gui.cookies_panel import CookiesPanel

        self.cookies_panel = CookiesPanel(self.db, self)
        return self.cookies_panel

    def _init_websocket_panel(self):
        from equinox.gui.websocket_panel import WebSocketPanel

        self.websocket_panel = WebSocketPanel(self)
        return self.websocket_panel

    def _safe_refresh(self, panel: object) -> None:
        """Call ``panel.refresh()`` if the panel exists, swallowing any error."""
        if panel is None:
            return
        try:
            panel.refresh()  # type: ignore[union-attr]
        except Exception:
            logger.debug("Panel refresh failed for %r", panel, exc_info=True)

    def _left_panel_for_index(self, index: int) -> object:
        if index == 0:
            return self.collections_panel
        if index == 1:
            return self.history_panel
        if index == 2:
            return self.variables_panel
        if index == 3:
            return self.logging_panel
        if index == 4:
            return self.cookies_panel
        if index == 5:
            return self.websocket_panel
        return None

    def _flush_pending_panel_refresh(self, index: int) -> None:
        """Apply one queued refresh for a panel when its tab becomes active."""
        if index not in self._pending_panel_refreshes:
            return
        panel = self._left_panel_for_index(index)
        if panel is None:
            return
        self._pending_panel_refreshes.discard(index)
        self._safe_refresh(panel)

    def _on_left_tab_changed(self, index: int) -> None:
        """Refresh deferred side panels when the user activates their tab."""
        self._flush_pending_panel_refresh(index)

    def _refresh_side_panel_on_response(self, index: int, panel: object) -> None:
        """Refresh panel now if visible, otherwise defer until tab activation."""
        if panel is None:
            self._pending_panel_refreshes.add(index)
            return
        if self._left_tabs.currentIndex() == index and self._left_tabs.isVisible():
            self._safe_refresh(panel)
            return
        self._pending_panel_refreshes.add(index)
