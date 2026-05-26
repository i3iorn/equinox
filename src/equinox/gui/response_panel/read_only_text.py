"""Read-only monospaced text editor widget.

Provides a QTextEdit subclass configured for displaying code/text
with syntax highlighting. Optimized for large response bodies.
"""

from __future__ import annotations

import logging

from PyQt6.QtWidgets import QTextEdit

from equinox.gui.theme import get_mono_font

logger = logging.getLogger(__name__)

# UI Configuration constants
_LINE_WRAP_MODE = QTextEdit.LineWrapMode.NoWrap
_UNDO_REDO_ENABLED = False
_SCROLL_TO_TOP = 0


class ReadOnlyText(QTextEdit):
    """Read-only monospaced text editor for displaying code and response bodies.

    Features:
    - Monospaced font for code display
    - No line wrapping (horizontal scroll)
    - Optimized for large bodies (no undo/redo history)
    - Batch updates prevent signal spam
    - Automatic scroll to top on content change
    """

    def __init__(self, parent: QTextEdit | None = None) -> None:
        """Initialize read-only text editor.

        Args:
            parent: Parent widget
        """
        super().__init__(parent)
        self._init_config()

    def _init_config(self) -> None:
        """Configure text editor for read-only code display."""
        self.setReadOnly(True)
        self.setFont(get_mono_font())
        self.setLineWrapMode(_LINE_WRAP_MODE)

        # Disable undo/redo to avoid allocating history for large bodies
        self.setUndoRedoEnabled(_UNDO_REDO_ENABLED)

    def set_code(self, text: object | None) -> None:
        """Set editor content with batched updates.

        Replaces entire document content while batching signals to prevent
        multiple updates to connected slots (e.g., search highlighting).
        Automatically scrolls to top after content change.

        Args:
            text: Code/text to display (str or str-like)

        Raises:
            TypeError: If text is not string-convertible (logged, not raised)
        """
        if text is None:
            logger.warning("set_code called with None, using empty string")
            text = ""

        # Convert to string if needed
        if not isinstance(text, str):
            try:
                text = str(text)
            except Exception as e:
                logger.exception("Failed to convert text to string: %s", e)
                text = ""

        # Block signals during bulk update
        self.blockSignals(True)
        try:
            self.setPlainText(text)
        except Exception as e:
            logger.exception("Failed to set plain text: %s", e)
            # Set empty on error
            try:
                self.setPlainText("")
            except Exception:
                pass
        finally:
            # Always unblock signals and reset scroll position
            self.blockSignals(False)
            self._scroll_to_top()

    def _scroll_to_top(self) -> None:
        """Scroll to top of document.

        Always done after content change to show beginning of text.
        Errors are logged but not raised to caller.
        """
        try:
            scrollbar = self.verticalScrollBar()
            if scrollbar is not None:
                scrollbar.setValue(_SCROLL_TO_TOP)
        except Exception as e:
            logger.debug("Failed to scroll to top: %s", e)
