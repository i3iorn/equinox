from PyQt6.QtWidgets import QTextEdit

from equinox.gui.theme import get_mono_font


class ReadOnlyText(QTextEdit):
    """Read-only monospaced text editor."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setFont(get_mono_font())
        self.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)

    def set_code(self, text: str) -> None:
        self.setPlainText(text)
        self.verticalScrollBar().setValue(0)
