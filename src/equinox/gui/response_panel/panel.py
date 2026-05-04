"""ResponsePanel — main widget for displaying HTTP responses.

Orchestrates display of HTTP response data across multiple tabs.
Delegates UI construction and data display to mixin classes:

- ``ResponseBuilderMixin``  — UI construction (``_build_*`` methods)
- ``ResponseDisplayMixin``  — populating tabs with response data
- ``ResponseActionsMixin``  — user actions (codegen, copy, diff, search …)

This module wires them together and owns the response display pipeline.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Optional

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QMessageBox
from PyQt6.QtCore import QThreadPool, QSettings

from equinox.core.request import Response
from equinox.gui.response_panel.builder import ResponseBuilderMixin
from equinox.gui.response_panel.display_mixin import ResponseDisplayMixin
from equinox.gui.response_panel.actions_mixin import ResponseActionsMixin

logger = logging.getLogger(__name__)

__all__ = ["ResponsePanel"]

# UI Configuration constants
_LARGE_BODY_THRESHOLD = 2_097_152  # 2 MB
_LAYOUT_MARGINS = (6, 4, 6, 4)
_LAYOUT_SPACING = 4

# View modes (used for view preference state)
_VIEW_MODE_RAW = "raw"
_VIEW_MODE_JSON = "json"

_READ_MODE_PRETTY = "pretty"
_READ_MODE_RAW = "raw"
_READ_MODE_SPLIT = "split"
_READ_MODE_DIFF = "diff"
_READABILITY_MODES = (_READ_MODE_PRETTY, _READ_MODE_RAW, _READ_MODE_SPLIT, _READ_MODE_DIFF)
_KEY_READABILITY_PREFS = "response/readability_by_content_type"


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

    Delegates specific UI concerns to mixin classes:
    - ResponseBuilderMixin: UI widget construction
    - ResponseDisplayMixin: Populating widgets with response data
    - ResponseActionsMixin: User interactions (copy, export, etc.)

    Attributes:
        current_response: Currently displayed Response object
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        """Initialize ResponsePanel.

        Args:
            parent: Parent widget (for PyQt6 ownership)
        """
        super().__init__(parent)
        # Response state
        self.current_response: Optional[Response] = None

        # Configuration
        self._LARGE_BODY_THRESHOLD = _LARGE_BODY_THRESHOLD

        # Thread pool for async operations (e.g., response intelligence)
        self._thread_pool = QThreadPool.globalInstance()
        self._settings = QSettings("Equinox", "Equinox")

        # View state
        self._body_highlighter: Optional[Any] = None
        self._view_preference = _VIEW_MODE_RAW  # Preferred view: "raw" or "json"
        self._readability_mode = _READ_MODE_PRETTY
        self._raw_body_text = ""
        self._pretty_body_text = ""
        self._readability_by_type = self._load_readability_preferences()

        # Initialize UI
        self._init_ui()

    # ------------------------------------------------------------------
    # Initialization & Setup
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
    # Response Display Pipeline
    # ------------------------------------------------------------------

    def display_response(self, response: Response) -> None:
        """Display an HTTP response.

        Main entry point for displaying responses. Orchestrates the complete
        display pipeline with comprehensive error handling.

        Args:
            response: Response object to display

        Raises:
            ValueError: If response is None
        """
        if response is None:
            logger.warning("display_response: called with None response")
            return

        logger.debug(
            "display_response: status=%d size=%d content_type=%s",
            response.status_code,
            response.size,
            response.headers.get("content-type", ""),
        )

        try:
            self._render_response(response)
        except Exception:
            logger.exception("display_response: unhandled exception in render pipeline")
            self._show_error_dialog(
                "Display Error",
                "An unexpected error occurred while displaying the response. See logs for details.",
            )

    def _render_response(self, response: Response) -> None:
        """Execute the complete response display pipeline.

        Pipeline steps:
        1. Store current response
        2. Update status bar (immediate visual feedback)
        3. Apply syntax highlighting
        4. Populate all tabs with response data
        5. Apply view preference
        6. Force widget refresh

        Args:
            response: Response to display
        """
        self.current_response = response

        # Execute display pipeline with error isolation
        self._safe_display(self._update_status_bar, response)
        self._safe_display(self._apply_highlighter_for_response, response)
        self._safe_display(self._populate_all_tabs, response)
        self._safe_display(self._apply_readability_mode_for_response, response)

        # Apply user's preferred view mode
        self._apply_view_preference()

        # Force Qt to refresh widgets after content updates
        self._refresh_display()

        logger.debug("display_response: pipeline complete")

    def _load_readability_preferences(self) -> dict:
        """Load saved readability preferences from QSettings."""
        raw = self._settings.value(_KEY_READABILITY_PREFS, "{}")
        try:
            data = json.loads(raw) if isinstance(raw, str) else {}
            if isinstance(data, dict):
                return {
                    str(k): str(v)
                    for k, v in data.items()
                    if str(v) in _READABILITY_MODES
                }
        except Exception:
            logger.debug("Failed to parse readability preferences", exc_info=True)
        return {}

    def _save_readability_preferences(self) -> None:
        """Persist readability preferences to QSettings."""
        try:
            self._settings.setValue(_KEY_READABILITY_PREFS, json.dumps(self._readability_by_type))
        except Exception:
            logger.debug("Failed to save readability preferences", exc_info=True)

    @staticmethod
    def _content_type_family(content_type: str) -> str:
        ct = (content_type or "").lower()
        if "json" in ct:
            return "json"
        if "xml" in ct:
            return "xml"
        if "html" in ct:
            return "html"
        if "text" in ct:
            return "text"
        return "other"

    def _apply_readability_mode_for_response(self, response: Response) -> None:
        """Apply the saved readability mode for the response content type."""
        family = self._content_type_family(response.headers.get("content-type", ""))
        mode = self._readability_by_type.get(family, _READ_MODE_PRETTY)
        self._switch_readability_mode(mode, persist=False)

    def _on_readability_selected(self, mode: str) -> None:
        """Handle readability mode selection from the toolbar menu."""
        self._switch_readability_mode(mode, persist=True)

    def _switch_readability_mode(self, mode: str, persist: bool = True) -> None:
        """Switch response body readability mode and update checked actions."""
        if mode not in _READABILITY_MODES:
            mode = _READ_MODE_PRETTY
        self._readability_mode = mode

        for key, action in getattr(self, "_readability_actions", {}).items():
            action.blockSignals(True)
            action.setChecked(key == mode)
            action.blockSignals(False)

        if self.current_response is not None:
            self._render_body_by_mode(mode)

            if persist:
                family = self._content_type_family(
                    self.current_response.headers.get("content-type", "")
                )
                self._readability_by_type[family] = mode
                self._save_readability_preferences()


    def _apply_highlighter_for_response(self, response: Response) -> None:
        """Apply syntax highlighter based on response content-type.

        Safely applies the appropriate syntax highlighter for the response body.
        Errors are logged but don't interrupt the display pipeline.

        Args:
            response: Response with headers
        """
        try:
            content_type = response.headers.get("content-type", "")
            self._apply_highlighter(content_type)
        except Exception:
            logger.exception(
                "apply_highlighter_for_response: failed for content-type=%r",
                response.headers.get("content-type", ""),
            )

    def _populate_all_tabs(self, response: Response) -> None:
        """Populate all response tabs with data.

        Displays response data across all tabs with independent error handling
        for each tab, ensuring one failure doesn't prevent displaying others.

        Args:
            response: Response to display
        """
        logger.debug(
            "populate_all_tabs: tabs_count=%d current_index=%d visible=%s",
            self.tabs.count(),
            self.tabs.currentIndex(),
            self.tabs.isVisible(),
        )

        # Each tab is populated independently with error isolation
        self._safe_display(self._display_body, response)
        self._safe_display(self._display_json_tree, response)
        self._safe_display(self._display_headers, response)
        self._safe_display(self._display_timings, response)
        self._safe_display(self._load_cookies_tab, response.headers)
        self._safe_display(self._display_sent_request, response)

        logger.debug("populate_all_tabs: completed")

    def _refresh_display(self) -> None:
        """Force Qt to refresh all widgets after content changes.

        Triggers update events on the tab widget and response panel
        to ensure all content changes are rendered.
        """
        try:
            logger.debug("refresh_display: triggering widget updates")
            self.tabs.update()
            if hasattr(self.body_text, 'update'):
                self.body_text.update()
            if hasattr(self.body_text, 'viewport'):
                self.body_text.viewport().update()
            self.update()
        except Exception:
            logger.debug("refresh_display: error during widget refresh", exc_info=True)

    # ------------------------------------------------------------------
    # View Mode Management
    # ------------------------------------------------------------------

    def _apply_view_preference(self) -> None:
        """Apply user's preferred view mode (raw or JSON).

        Attempts to switch to the preferred view, falling back to raw view
        if JSON view is not available.
        """
        if self._view_preference == _VIEW_MODE_JSON and self._view_json_act.isEnabled():
            self._switch_view(_VIEW_MODE_JSON)
        else:
            self._switch_view(_VIEW_MODE_RAW)

    def _on_view_selected(self, mode: str) -> None:
        """Handle user view mode selection.

        Called when user selects a different view mode (raw or JSON).

        Args:
            mode: "json" for JSON view, "raw" for raw view
        """
        if mode not in (_VIEW_MODE_JSON, _VIEW_MODE_RAW):
            logger.warning("on_view_selected: invalid mode=%r, using raw", mode)
            mode = _VIEW_MODE_RAW

        self._view_preference = mode
        self._switch_view(mode)

    def _switch_view(self, mode: str) -> None:
        """Switch to the specified view mode.

        Internal method that performs the actual view switch by:
        - Updating action checked states
        - Switching to the appropriate tab

        Args:
            mode: "json" or "raw"
        """
        is_json = mode == _VIEW_MODE_JSON

        # Update action states (signal blocking prevents loops)
        self._view_json_act.blockSignals(True)
        self._view_raw_act.blockSignals(True)
        try:
            self._view_json_act.setChecked(is_json)
            self._view_raw_act.setChecked(not is_json)
        finally:
            self._view_json_act.blockSignals(False)
            self._view_raw_act.blockSignals(False)

        # Switch to appropriate tab
        tab_idx = self._json_tab_idx if is_json else self._body_tab_idx
        self.tabs.setCurrentIndex(tab_idx)
        logger.debug("switch_view: switched to %s view (tab_idx=%d)", mode, tab_idx)

    # ------------------------------------------------------------------
    # Utility Methods & Error Handling
    # ------------------------------------------------------------------

    def set_intelligence_badge(self, count: int) -> None:
        """Update intelligence panel tab label with finding count.

        Displays a badge with the number of findings, or hides it if count is 0.

        Args:
            count: Number of intelligence findings (≥ 0)
        """
        try:
            idx = self.tabs.indexOf(self.intelligence_panel)
            if idx < 0:
                logger.debug("set_intelligence_badge: intelligence panel not found in tabs")
                return

            label = f"Intelligence ({count})" if count > 0 else "Intelligence"
            self.tabs.setTabText(idx, label)
            logger.debug("set_intelligence_badge: updated to count=%d", count)
        except Exception:
            logger.exception("set_intelligence_badge: failed to update badge")

    def _show_error_dialog(self, title: str, message: str) -> None:
        """Show an error dialog to the user.

        Safely displays an error message, handling any dialog creation failures.

        Args:
            title: Dialog title
            message: Error message to display
        """
        try:
            QMessageBox.critical(self, title, message)
        except Exception:
            logger.debug(
                "show_error_dialog: failed to display dialog (title=%r)",
                title,
                exc_info=True,
            )

    @staticmethod
    def _safe_display(fn: Callable[[Any], None], *args: Any) -> None:
        """Safely call a display function with comprehensive error isolation.

        Wraps a display function call in try-catch so that one failure
        doesn't interrupt the rest of the display pipeline. Particularly
        useful when displaying different response tabs — if headers fail,
        we still want to show the body.

        Args:
            fn: Display function to call (should accept *args)
            *args: Arguments to pass to fn

        Logs:
            DEBUG: On successful completion
            EXCEPTION: On any error in fn
        """
        try:
            fn(*args)
            logger.debug("safe_display: %s completed successfully", fn.__name__)
        except Exception:
            logger.exception("safe_display: %s failed with error", fn.__name__)

