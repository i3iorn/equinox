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
from collections.abc import Callable
from typing import Any
from typing import cast

from equinox.core.request import Response
from equinox.gui import ui_common
from equinox.gui.response_panel.actions_mixin import ResponseActionsMixin
from equinox.gui.response_panel.builder import ResponseBuilderMixin
from equinox.gui.response_panel.display_mixin import ResponseDisplayMixin
from PyQt6.QtCore import QThreadPool
from PyQt6.QtWidgets import QLabel
from PyQt6.QtWidgets import QMessageBox
from PyQt6.QtWidgets import QVBoxLayout
from PyQt6.QtWidgets import QWidget

logger = logging.getLogger(__name__)

__all__ = ["ResponsePanel"]

# UI Configuration constants
_LARGE_BODY_THRESHOLD = 2_097_152  # 2 MB
_MAX_RENDER_BODY_SIZE = 10 * 1024 * 1024  # 10 MB hard cap for in-editor rendering
_LARGE_BODY_PREVIEW_BYTES = 256 * 1024  # 256 KB preview when body exceeds hard cap
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
_KEY_REDACTION_PREVIEW = "response/redaction_preview"
_KEY_ACTIVE_TAB = "response/active_tab"


class ResponsePanel(
    ResponseBuilderMixin,  # type: ignore[misc]
    ResponseDisplayMixin,  # type: ignore[misc]
    ResponseActionsMixin,  # type: ignore[misc]
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

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize ResponsePanel.

        Args:
            parent: Parent widget (for PyQt6 ownership)
        """
        super().__init__(parent)
        # Response state
        self.current_response: Response | None = None

        # Configuration
        self._LARGE_BODY_THRESHOLD = _LARGE_BODY_THRESHOLD
        self._MAX_RENDER_BODY_SIZE = _MAX_RENDER_BODY_SIZE
        self._LARGE_BODY_PREVIEW_BYTES = _LARGE_BODY_PREVIEW_BYTES

        # Thread pool for async operations (e.g., response intelligence)
        self._thread_pool = QThreadPool.globalInstance()
        self._settings = ui_common.get_gui_settings()

        # View state
        self._body_highlighter: Any | None = None
        self._view_preference = _VIEW_MODE_RAW  # Preferred view: "raw" or "json"
        self._readability_mode = _READ_MODE_PRETTY
        self._redaction_preview = bool(
            self._settings.value(_KEY_REDACTION_PREVIEW, False, type=bool),
        )
        self._raw_body_text = ""
        self._pretty_body_text = ""
        self._total_header_count = 0
        self._readability_by_type = self._load_readability_preferences()
        self._suppress_tab_sync = False

        # Initialize UI
        self._init_ui()

    def _as_qwidget(self) -> QWidget:
        """Return this panel cast as QWidget for Qt dialog APIs."""
        return cast(QWidget, self)

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
        self._render_warning_label = QLabel("")
        self._render_warning_label.setObjectName("field-error")
        self._render_warning_label.setVisible(False)
        layout.addWidget(self._render_warning_label)
        self._build_tabs(layout)
        ui_common.configure_tab_persistence(
            self.tabs,
            settings_key=_KEY_ACTIVE_TAB,
            default_tab="Body",
            settings=self._settings,
        )
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self._redact_btn.setChecked(self._redaction_preview)

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
        failed_sections = []
        steps = (
            ("status", self._update_status_bar),
            ("highlight", self._apply_highlighter_for_response),
            ("tabs", self._populate_all_tabs),
            ("mode", self._apply_readability_mode_for_response),
        )
        for name, fn in steps:
            if not self._safe_display(fn, response):
                failed_sections.append(name)

        # Apply user's preferred view mode
        self._apply_view_preference()

        # Force Qt to refresh widgets after content updates
        self._refresh_display()
        self._update_render_warning(failed_sections)

        logger.debug("display_response: pipeline complete")

    def _on_redaction_toggled(self, checked: bool) -> None:
        """Toggle sensitive-data redaction preview and re-render current response."""
        self._redaction_preview = bool(checked)
        try:
            self._settings.setValue(_KEY_REDACTION_PREVIEW, self._redaction_preview)
        except Exception:
            logger.exception("Failed to persist redaction preview setting", exc_info=True)

        if self.current_response is not None:
            failed_sections = []
            if not self._safe_display(self._populate_all_tabs, self.current_response):
                failed_sections.append("tabs")
            if not self._safe_display(
                self._apply_readability_mode_for_response,
                self.current_response,
            ):
                failed_sections.append("mode")
            self._update_render_warning(failed_sections)

    def _update_render_warning(self, failed_sections: list[str]) -> None:
        """Show or clear a non-blocking render warning banner."""
        if not failed_sections:
            self._render_warning_label.setVisible(False)
            self._render_warning_label.setText("")
            return
        unique = ", ".join(sorted(set(failed_sections)))
        self._render_warning_label.setText(
            f"Some sections failed to render ({unique}). See logs for details.",
        )
        self._render_warning_label.setVisible(True)

    def _load_readability_preferences(self) -> dict[str, str]:
        """Load saved readability preferences from QSettings."""
        raw = self._settings.value(_KEY_READABILITY_PREFS, "{}")
        try:
            data = json.loads(raw) if isinstance(raw, str) else {}
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items() if str(v) in _READABILITY_MODES}
        except Exception:
            logger.exception("Failed to parse readability preferences", exc_info=True)
        return {}

    def _save_readability_preferences(self) -> None:
        """Persist readability preferences to QSettings."""
        try:
            self._settings.setValue(_KEY_READABILITY_PREFS, json.dumps(self._readability_by_type))
        except Exception:
            logger.exception("Failed to save readability preferences", exc_info=True)

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
                    self.current_response.headers.get("content-type", ""),
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
        self._safe_display(self._display_connection_details, response)

        logger.debug("populate_all_tabs: completed")

    def _refresh_display(self) -> None:
        """Force Qt to refresh all widgets after content changes.

        Triggers update events on the tab widget and response panel
        to ensure all content changes are rendered.
        """
        try:
            logger.debug("refresh_display: triggering widget updates")
            self.tabs.update()
            if hasattr(self.body_text, "update"):
                self.body_text.update()
            if hasattr(self.body_text, "viewport"):
                viewport = self.body_text.viewport()
                if viewport is not None:
                    viewport.update()
            self.update()
        except Exception:
            logger.exception("refresh_display: error during widget refresh", exc_info=True)

    # ------------------------------------------------------------------
    # View Mode Management
    # ------------------------------------------------------------------

    def _apply_view_preference(self) -> None:
        """Apply user's preferred view mode (raw or JSON).

        Attempts to switch to the preferred view, falling back to raw view
        if JSON view is not available.
        """
        if self._view_json_act is None or self._view_raw_act is None:
            return

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
        if self._view_json_act is None or self._view_raw_act is None:
            return

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
        self._suppress_tab_sync = True
        try:
            self.tabs.setCurrentIndex(tab_idx)
        finally:
            self._suppress_tab_sync = False
        self._on_tab_changed(tab_idx)
        logger.debug("switch_view: switched to %s view (tab_idx=%d)", mode, tab_idx)

    def _on_tab_changed(self, idx: int) -> None:
        """Keep view actions in sync with selected tab and lazy-load JSON tree."""
        if idx == self._json_tab_idx:
            self._json_tree.ensure_loaded()

        if self._view_json_act is None or self._view_raw_act is None:
            return

        if getattr(self, "_suppress_tab_sync", False):
            return

        if idx not in (self._json_tab_idx, self._body_tab_idx):
            return

        is_json = idx == self._json_tab_idx
        self._view_json_act.blockSignals(True)
        self._view_raw_act.blockSignals(True)
        try:
            self._view_json_act.setChecked(is_json)
            self._view_raw_act.setChecked(not is_json)
        finally:
            self._view_json_act.blockSignals(False)
            self._view_raw_act.blockSignals(False)

        self._view_preference = _VIEW_MODE_JSON if is_json else _VIEW_MODE_RAW

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
            logger.exception(
                "show_error_dialog: failed to display dialog (title=%r)",
                title,
                exc_info=True,
            )

    @staticmethod
    def _safe_display(fn: Callable[[Any], None], *args: Any) -> bool:
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
            return True
        except Exception:
            logger.exception("safe_display: %s failed with error", fn.__name__)
            return False
