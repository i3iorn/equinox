"""Background pretty-printer and content-type → highlighter mapping."""

from __future__ import annotations

import json
import logging

from PyQt6.QtCore import QObject, QRunnable, pyqtSignal

from equinox.core.request import Response
from equinox.gui.syntax_highlighter import JsonHighlighter, XmlHighlighter, YamlHighlighter

logger = logging.getLogger(__name__)

# Maps content-type substrings (checked in order) to highlighter classes.
# First match wins, so more specific tokens should come first.
CT_HIGHLIGHTERS = [
    ("json", JsonHighlighter),
    ("xml",  XmlHighlighter),
    ("html", XmlHighlighter),
    ("svg",  XmlHighlighter),
    ("yaml", YamlHighlighter),
    ("yml",  YamlHighlighter),
]


class WorkerSignals(QObject):
    result = pyqtSignal(object, str)  # (marker, formatted_text)


class PrettyPrintRunnable(QRunnable):
    """Runs JSON/XML pretty-printing off the UI thread."""

    def __init__(self, response: Response, marker: str):
        super().__init__()
        self.response = response
        self.marker = marker
        self.signals = WorkerSignals()

    def run(self) -> None:
        try:
            text = self._format_body()
        except Exception:
            text = getattr(self.response, "text", "") or ""
        try:
            self.signals.result.emit(self.marker, text)
        except Exception:
            # If the UI is gone, just drop the result
            pass

    def _format_body(self) -> str:
        # JSON
        if getattr(self.response, "is_json", False):
            try:
                return json.dumps(self.response.json(), indent=2, ensure_ascii=False)
            except Exception:
                pass

        # XML / HTML
        ct = self.response.headers.get("content-type", "").lower()
        if any(x in ct for x in ("xml", "html", "svg")):
            try:
                import xml.dom.minidom
                return xml.dom.minidom.parseString(
                    self.response.text.encode()
                ).toprettyxml(indent="  ")
            except Exception:
                pass

        # Fallback
        return self.response.text

