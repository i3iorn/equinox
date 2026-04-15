"""ResponsePanel — main widget for displaying HTTP responses.

Orchestrates display of HTTP response data across multiple tabs.
Delegates UI construction and data display to mixin classes:

- ``ResponseBuilderMixin``  — UI construction (``_build_*`` methods)
- ``ResponseDisplayMixin``  — populating tabs with response data
- ``ResponseActionsMixin``  — user actions (codegen, copy, diff, search …)

This module wires them together and owns the response display pipeline.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QMessageBox
from PyQt6.QtCore import QThreadPool

from equinox.core.request import Response
from equinox.gui.response_panel.builder import ResponseBuilderMixin
from equinox.gui.response_panel.display_mixin import ResponseDisplayMixin
from equinox.gui.response_panel.actions_mixin import ResponseActionsMixin
from equinox.gui.response_panel._formatting import pretty_print_body

logger = logging.getLogger(__name__)

__all__ = ["ResponsePanel"]

# UI Configuration constants
_LARGE_BODY_THRESHOLD = 2_097_152  # 2 MB
_LAYOUT_MARGINS = (6, 4, 6, 4)
_LAYOUT_SPACING = 4


class ResponsePanel(
    ResponseBuilderMixin,
    ResponseDisplayMixin,
    ResponseActionsMixin,
    QWidget,
):
    """Panel for displaying HTTP responses and the request that was sent.

    Manages the complete response display pipeline including:
    - Status bar and metadata
    - Response body with syntax highlighting
    - Headers, cookies, timings
    - Sent request details
    - View switching (raw/JSON)
    - Intelligence findings

    Delegates specific concerns to mixin classes.
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.current_response: Optional[Response] = None
        self._thread_pool = QThreadPool.globalInstance()
        self._body_highlighter: Optional[Any] = None
        self._prefer_json_view = False
        self._LARGE_BODY_THRESHOLD = _LARGE_BODY_THRESHOLD

        self._init_ui()

    # ------------------------------------------------------------------
    # UI bootstrap
    # ------------------------------------------------------------------

    def _init_ui(self) -> None:
        """Initialize UI components and layout."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(*_LAYOUT_MARGINS)
        layout.setSpacing(_LAYOUT_SPACING)

        # Build tab structure from mixins
        self._build_status_bar(layout)
        self._build_timings_row(layout)
        self._build_tabs(layout)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def display_response(self, response: Response) -> None:
        """Display an HTTP response.

        Main entry point for displaying responses. Safely updates all
        display elements and handles errors gracefully.

        Args:
            response: Response object to display

        Raises:
            ValueError: If response is None (validation)
        """
        if response is None:
            logger.warning("display_response called with None response")
            return

        try:
            self._display_response_impl(response)
        except Exception:
            logger.exception("Unhandled exception in display_response")
            self._show_display_error()

    def _display_response_impl(self, response: Response) -> None:
        """Implementation of response display pipeline.

        Args:
            response: Response to display
        """
        self.current_response = response

        logger.debug(
            "display_response: status=%s size=%s",
            getattr(response, "status_code", None),
            getattr(response, "size", None),
        )

        # Apply syntax highlighting for body
        self._apply_highlighter_safe(response)

        # Display each section of the response (all guarded)
        self._display_sections(response)

        # Switch to preferred view
        self._apply_view_preference()

    def _apply_highlighter_safe(self, response: Response) -> None:
        """Apply syntax highlighter for response content-type.

        Args:
            response: Response with headers
        """
        try:
            content_type = response.headers.get("content-type", "")
            self._apply_highlighter(content_type)
        except Exception:
            logger.exception(
                "Failed to apply highlighter for content-type=%s",
                response.headers.get("content-type", ""),
            )

    def _display_sections(self, response: Response) -> None:
        """Display all response sections with error isolation.

        Each section is displayed independently so one failure
        doesn't prevent displaying the rest.

        Args:
            response: Response to display
        """
        self._safe_display(self._display_body, response)
        self._safe_display(self._display_json_tree, response)
        self._safe_display(self._display_headers, response)
        self._safe_display(self._display_timings, response)
        self._safe_display(self._load_cookies_tab, response.headers)
        self._safe_display(self._display_sent_request, response)

    def _apply_view_preference(self) -> None:
        """Apply user's preferred view (raw or JSON).

        Falls back to raw view if JSON view is not available.
        """
        if self._prefer_json_view and self._view_json_act.isEnabled():
            self._switch_to_json_view()
        else:
            self._switch_to_raw_view()

    def _show_display_error(self) -> None:
        """Show error dialog when response display fails.

        Handles dialog creation failures gracefully.
        """
        try:
            QMessageBox.critical(
                self,
                "Display Error",
                "An unexpected error occurred while displaying the response. "
                "See logs for details.",
            )
        except Exception:
            logger.debug(
                "Failed to show error dialog",
                exc_info=True,
            )

    def _pretty_body(self, response: Response) -> str:
        """Format response body for display (JSON or XML, otherwise raw).

        Wrapper around the pretty_print_body function from _formatting.

        Args:
            response: Response object to format

        Returns:
            Formatted body string
        """
        return pretty_print_body(response)

    def set_intelligence_badge(self, count: int) -> None:
        """Set badge showing number of intelligence findings.

        Args:
            count: Number of findings (0 = no badge)
        """
        idx = self.tabs.indexOf(self.intelligence_panel)
        if idx < 0:
            logger.debug("Intelligence panel not found in tabs")
            return

        label = f"Intelligence ({count})" if count > 0 else "Intelligence"
        self.tabs.setTabText(idx, label)

    # ------------------------------------------------------------------
    # View switching
    # ------------------------------------------------------------------

    def _on_view_selected(self, mode: str) -> None:
        """Handle view mode selection (raw or JSON).

        Args:
            mode: "json" for JSON view, anything else for raw
        """
        if mode == "json":
            self._prefer_json_view = True
            self._switch_to_json_view()
        else:
            self._prefer_json_view = False
            self._switch_to_raw_view()

    def _switch_to_raw_view(self) -> None:
        """Switch to raw response body view."""
        self._view_raw_act.setChecked(True)
        self._view_json_act.setChecked(False)
        self.tabs.setCurrentIndex(self._body_tab_idx)

    def _switch_to_json_view(self) -> None:
        """Switch to JSON tree view (with fallback to raw).

        Falls back to raw view if JSON view is not available.
        """
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
    def _safe_display(fn: Callable[[Any], None], *args: Any) -> None:
        """Safely call a display function, isolating errors.

        If a display function fails, the error is logged but other
        display functions continue. This prevents one broken section
        from hiding the rest of the response.

        Args:
            fn: Display function to call
            *args: Arguments to pass to fn
        """
        try:
            fn(*args)
        except Exception:
            logger.exception("Display function %s failed", fn.__name__)

