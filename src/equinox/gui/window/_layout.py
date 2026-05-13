"""Layout persistence mixin for MainWindow.

Handles saving and restoring window geometry, splitter sizes, and tab
selection between sessions via QSettings.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PyQt6.QtCore import QByteArray

from equinox.gui.logging_utils import log_gui_event

if TYPE_CHECKING:  # pragma: no cover
    pass

logger = logging.getLogger(__name__)

# ── QSettings keys ────────────────────────────────────────────────────────────
_KEY_GEOMETRY = "window/geometry"
_KEY_WIN_STATE = "window/state"
_KEY_MAIN_SPLIT = "splitter/main"
_KEY_REQRESP_SPLIT = "splitter/req_resp"
_KEY_LEFT_TAB = "left_tabs/index"


class _LayoutMixin:
    """Save and restore window geometry, splitter positions, and active tab."""

    def _restore_layout(self) -> None:
        """Restore window geometry and splitter sizes from QSettings."""
        geo = self._settings.value(_KEY_GEOMETRY)
        if isinstance(geo, QByteArray):
            self.restoreGeometry(geo)
            logger.debug("Restored window geometry")
            log_gui_event("window_geometry_restored", {"geometry_present": True})

        state = self._settings.value(_KEY_WIN_STATE)
        if isinstance(state, QByteArray):
            self.restoreState(state)
            logger.debug("Restored window state")
            log_gui_event("window_state_restored")

        ms = self._settings.value(_KEY_MAIN_SPLIT)
        if ms is not None:
            try:
                sizes = [int(x) for x in ms]
                self._main_splitter.setSizes(sizes)
                logger.debug("Restored main splitter sizes: %s", sizes)
                log_gui_event("window_main_splitter_restored", {"sizes": sizes})
            except Exception as e:
                logger.debug("Failed to restore main splitter sizes: %s", e, exc_info=True)
        else:
            logger.debug("No saved main splitter sizes found in QSettings")

        rs = self._settings.value(_KEY_REQRESP_SPLIT)
        if rs is not None:
            try:
                sizes = [int(x) for x in rs]
                self._req_resp_splitter.setSizes(sizes)
                logger.debug("Restored req/resp splitter sizes: %s", sizes)
                log_gui_event("window_req_resp_splitter_restored", {"sizes": sizes})
            except Exception as e:
                logger.debug("Failed to restore req/resp splitter sizes: %s", e, exc_info=True)
        else:
            logger.debug("No saved req/resp splitter sizes found in QSettings")

        tab_idx = self._settings.value(_KEY_LEFT_TAB, 0, type=int)
        # Block signals so setCurrentIndex doesn't fire _ensure_tab_initialized
        # synchronously during __init__ — defer to the first event-loop iteration.
        self._left_tabs.blockSignals(True)
        self._left_tabs.setCurrentIndex(tab_idx)
        log_gui_event("window_left_tab_restored", {
            "index": tab_idx,
            "tab": self._left_tabs.tabText(tab_idx),
        })
        self._left_tabs.blockSignals(False)
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(0, lambda: self._ensure_tab_initialized(self._left_tabs.currentIndex()))
        logger.debug("Restored left tabs index: %d (initialization deferred)", tab_idx)

    def _save_layout(self) -> None:
        """Persist window geometry and splitter sizes."""
        try:
            self._settings.setValue(_KEY_GEOMETRY, self.saveGeometry())
            logger.debug("Saved window geometry")
            self._settings.setValue(_KEY_WIN_STATE, self.saveState())
            logger.debug("Saved window state")
            main_sizes = list(self._main_splitter.sizes())
            self._settings.setValue(_KEY_MAIN_SPLIT, main_sizes)
            logger.debug("Saved main splitter sizes: %s", main_sizes)
            req_resp_sizes = list(self._req_resp_splitter.sizes())
            self._settings.setValue(_KEY_REQRESP_SPLIT, req_resp_sizes)
            logger.debug("Saved req/resp splitter sizes: %s", req_resp_sizes)
            tab_idx = self._left_tabs.currentIndex()
            self._settings.setValue(_KEY_LEFT_TAB, tab_idx)
            logger.debug("Saved left tabs index: %d", tab_idx)
            self._settings.sync()
            logger.debug("Layout settings synchronized to disk")
        except Exception as e:
            logger.error("Failed to save layout: %s", e, exc_info=True)

