"""URL input with ghost query-parameter preview."""


from PyQt6.QtWidgets import QLineEdit
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPainter, QColor, QFontMetricsF

from equinox.gui.theme import Colors


class UrlLineEdit(QLineEdit):
    """URL bar that renders active query parameters as a ghost suffix.

    When the field is **not** focused, enabled params from the Params table are
    painted after the URL text in ``Colors.FG_SUBTLE``, giving the user a live
    preview of the full request URL without polluting the actual text value.

    When the field **receives focus** the ghost is hidden so the user edits
    the clean URL string without visual clutter.
    """

    # Internal geometry constants — must match the QSS: border 1 px + padding 6 px
    _LEFT = 7
    _RIGHT = 7

    def __init__(self, parent=None):
        super().__init__(parent)
        self._param_suffix: str = ""

    def set_param_suffix(self, suffix: str) -> None:
        """Update the ghost params string and repaint."""
        if suffix != self._param_suffix:
            self._param_suffix = suffix
            self.update()

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.update()   # hide ghost while editing

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.update()   # show ghost again

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.hasFocus() or not self._param_suffix:
            return

        fm = QFontMetricsF(self.font())
        url_width = fm.horizontalAdvance(self.text())

        # If the URL is already scrolled (wider than visible area) the suffix
        # can't be reliably positioned, so skip it.
        visible_width = self.width() - self._LEFT - self._RIGHT
        if url_width > visible_width:
            return

        suffix_x = self._LEFT + int(url_width)
        available_width = self.width() - suffix_x - self._RIGHT
        if available_width < 16:
            return

        painter = QPainter(self)
        painter.setPen(QColor(Colors.FG_SUBTLE))
        painter.setFont(self.font())
        painter.drawText(
            suffix_x, 0, available_width, self.height(),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            self._param_suffix,
        )
        painter.end()

