"""Background pretty-printer and content-type → highlighter mapping.

Provides off-thread pretty-printing and content-type to syntax highlighter mapping.
Uses the centralized formatting module for actual formatting logic.
"""

from __future__ import annotations

import logging

from PyQt6.QtCore import QObject, QRunnable, pyqtSignal

from equinox.core.request import Response
from equinox.gui.response_panel._formatting import pretty_print_body
from equinox.gui.syntax_highlighter import JsonHighlighter, XmlHighlighter, YamlHighlighter

logger = logging.getLogger(__name__)

# Maps content-type substrings (checked in order) to highlighter classes.
# First match wins, so more specific tokens should come first.
CT_HIGHLIGHTERS = [
    ("json", JsonHighlighter),
    ("xml", XmlHighlighter),
    ("html", XmlHighlighter),
    ("svg", XmlHighlighter),
    ("yaml", YamlHighlighter),
    ("yml", YamlHighlighter),
]

# Worker thread result signal timeout (ms)
_SIGNAL_TIMEOUT_MS = 5000


class WorkerSignals(QObject):
    """Signals emitted by pretty-print worker thread.

    Signals:
        result: Emitted when formatting completes with (marker, formatted_text)
    """

    result = pyqtSignal(object, str)  # (marker: str, formatted_text: str)


class PrettyPrintRunnable(QRunnable):
    """Off-thread JSON/XML pretty-printer.

    Runs formatting in a background thread to prevent UI blocking on large
    responses. Emits result signal when complete.

    Delegates actual formatting to centralized formatting module.
    """

    def __init__(self, response: Response, marker: str) -> None:
        """Initialize pretty-printer worker.

        Args:
            response: Response to format
            marker: Marker to identify this result in UI

        Raises:
            ValueError: If response is None
        """
        super().__init__()

        if response is None:
            raise ValueError("response cannot be None")

        self.response = response
        self.marker = marker
        self.signals = WorkerSignals()

    def run(self) -> None:
        """Execute formatting and emit result signal.

        Catches all exceptions to prevent thread crashes. Errors are logged
        and fallback to raw text is used.
        """
        formatted_text = self._format_safely()
        self._emit_result(formatted_text)

    def _format_safely(self) -> str:
        """Format response body safely with fallback.

        Returns:
            Formatted text or raw text on error
        """
        try:
            return pretty_print_body(self.response)
        except Exception as e:
            logger.exception("Pretty-print formatting failed: %s", e)
            # Fallback to raw text
            return self._get_fallback_text()

    def _emit_result(self, formatted_text: str) -> None:
        """Emit result signal safely.

        If UI is gone or signal fails, error is logged but not raised.

        Args:
            formatted_text: Formatted text to emit
        """
        try:
            self.signals.result.emit(self.marker, formatted_text)
            logger.debug("Emitted pretty-print result for marker=%s", self.marker)
        except Exception as e:
            logger.debug("Failed to emit pretty-print result: %s", e)

    @staticmethod
    def _get_fallback_text() -> str:
        """Get fallback text when formatting fails.

        Returns:
            Empty string (UI will show placeholder)
        """
        return ""

