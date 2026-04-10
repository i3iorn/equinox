from PyQt6.QtWidgets import QTextEdit

from equinox.gui.theme import get_mono_font


class ReadOnlyText(QTextEdit):
    """Read-only monospaced text editor."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setFont(get_mono_font())
        self.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        # Read-only widgets never need undo/redo history; disabling it
        # avoids allocating an undo stack for potentially large bodies.
        self.setUndoRedoEnabled(False)

    def set_code(self, text: str) -> None:
        # Block signals while replacing the document content so that
        # connected slots (e.g. search-bar highlight refresh) are not
        # triggered on every intermediate state during the bulk update.
        self.blockSignals(True)
        try:
            self.setPlainText(text)
        finally:
            self.blockSignals(False)
        self.verticalScrollBar().setValue(0)
