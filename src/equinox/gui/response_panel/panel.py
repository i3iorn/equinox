"""ResponsePanel — main widget for displaying HTTP responses.

The heavy lifting is split across mixin classes:

- ``ResponseBuilderMixin``  — UI construction (``_build_*`` methods)
- ``ResponseDisplayMixin``  — populating tabs with response data
- ``ResponseActionsMixin``  — user actions (codegen, copy, diff, search …)

This module wires them together and owns ``display_response()``.
"""

from __future__ import annotations

import logging
from typing import Optional

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QMessageBox
from PyQt6.QtCore import QThreadPool

from equinox.core.request import Response
from equinox.gui.response_panel.builder import ResponseBuilderMixin
from equinox.gui.response_panel.display_mixin import ResponseDisplayMixin
from equinox.gui.response_panel.actions_mixin import ResponseActionsMixin

logger = logging.getLogger(__name__)

__all__ = ["ResponsePanel"]


class ResponsePanel(
    ResponseBuilderMixin,
    ResponseDisplayMixin,
    ResponseActionsMixin,
    QWidget,
):
    """Panel for displaying HTTP responses and the request that was sent."""

    _LARGE_BODY_THRESHOLD = 2_097_152  # 2 MB

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_response: Optional[Response] = None
        self._thread_pool = QThreadPool.globalInstance()
        self._body_highlighter = None
        self._prefer_json_view = False
        self._init_ui()

    # ------------------------------------------------------------------
    # UI bootstrap
    # ------------------------------------------------------------------

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(4)

        self._build_status_bar(layout)
        self._build_timings_row(layout)
        self._build_tabs(layout)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def display_response(self, response: Response) -> None:
        """Main entry point: display a new HTTP response."""
        try:
            self.current_response = response

            logger.debug(
                "display_response: status=%s size=%s",
                getattr(response, "status_code", None),
                getattr(response, "size", None),
            )

            self._update_status_bar(response)

            try:
                self._apply_highlighter(response.headers.get("content-type", ""))
            except Exception:
                logger.exception(
                    "_apply_highlighter raised for content-type=%s",
                    response.headers.get("content-type", ""),
                )

            # Each sub-step is guarded so one failure doesn't prevent the rest.
            self._safe_call(self._display_body, response)
            self._safe_call(self._display_json_tree, response)
            self._safe_call(self._display_headers, response)
            self._safe_call(self._display_timings, response)
            self._safe_call(self._load_cookies_tab, response.headers)
            self._safe_call(self._display_sent_request, response)

            if self._prefer_json_view and self._view_json_act.isEnabled():
                self._switch_to_json_view()
            else:
                self._switch_to_raw_view()
        except Exception:
            logger.exception("Unhandled exception in display_response")
            try:
                QMessageBox.critical(
                    self,
                    "Display Error",
                    "An unexpected error occurred while displaying the response. "
                    "See logs for details.",
                )
            except Exception:
                logger.debug(
                    "Failed to show error dialog after display_response exception",
                    exc_info=True,
                )

    def set_intelligence_badge(self, count: int) -> None:
        """Set a badge showing the number of intelligence findings."""
        idx = self.tabs.indexOf(self.intelligence_panel)
        if idx < 0:
            return
        label = f"Intelligence ({count})" if count > 0 else "Intelligence"
        self.tabs.setTabText(idx, label)

    # ------------------------------------------------------------------
    # View switching
    # ------------------------------------------------------------------

    def _on_view_selected(self, mode: str) -> None:
        if mode == "json":
            self._prefer_json_view = True
            self._switch_to_json_view()
        else:
            self._prefer_json_view = False
            self._switch_to_raw_view()

    def _switch_to_raw_view(self) -> None:
        self._view_raw_act.setChecked(True)
        self._view_json_act.setChecked(False)
        self.tabs.setCurrentIndex(self._body_tab_idx)

    def _switch_to_json_view(self) -> None:
        if not self._view_json_act.isEnabled():
            self._switch_to_raw_view()
            return
        self._view_raw_act.setChecked(False)
        self._view_json_act.setChecked(True)
        self.tabs.setCurrentIndex(self._json_tab_idx)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _safe_call(fn, *args) -> None:
        """Call *fn* and swallow + log any exception."""
        try:
            fn(*args)
        except Exception:
            logger.exception("%s failed", fn.__name__)

