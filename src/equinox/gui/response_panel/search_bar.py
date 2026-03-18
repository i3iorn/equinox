from PyQt6.QtGui import QTextCharFormat, QColor, QTextCursor, QTextDocument
from PyQt6.QtWidgets import QWidget, QTextEdit, QHBoxLayout, QLineEdit, QLabel, QToolButton

from equinox.gui.theme import Colors


class SearchBar(QWidget):
    """Inline find-bar for a QTextEdit — shown/hidden with Ctrl+F."""

    def __init__(self, target: QTextEdit, parent=None):
        super().__init__(parent)
        self._target = target
        self.setVisible(False)

        row = QHBoxLayout(self)
        row.setContentsMargins(2, 2, 2, 2)
        row.setSpacing(4)

        self._input = QLineEdit()
        self._input.setPlaceholderText("Find in body…")
        self._input.setFixedHeight(24)
        self._input.returnPressed.connect(self._find_next)
        self._input.textChanged.connect(self._on_text_changed)

        self._match_label = QLabel("")
        self._match_label.setObjectName("mutedLabel")

        prev_btn = QToolButton(); prev_btn.setText("▲"); prev_btn.setFixedSize(24, 24)
        next_btn = QToolButton(); next_btn.setText("▼"); next_btn.setFixedSize(24, 24)
        close_btn = QToolButton(); close_btn.setText("✕"); close_btn.setFixedSize(24, 24)
        close_btn.setStyleSheet(f"color: {Colors.FG_MUTED};")

        prev_btn.clicked.connect(self._find_prev)
        next_btn.clicked.connect(self._find_next)
        close_btn.clicked.connect(self.hide)

        row.addWidget(QLabel("Find:"))
        row.addWidget(self._input, 1)
        row.addWidget(self._match_label)
        row.addWidget(prev_btn)
        row.addWidget(next_btn)
        row.addWidget(close_btn)

    def show_and_focus(self):
        self.setVisible(True)
        self._input.selectAll()
        self._input.setFocus()

    def hide(self):
        self.setVisible(False)
        self._target.setExtraSelections([])   # clear yellow highlights
        self._target.setFocus()

    def _on_text_changed(self, _text):
        self._highlight_all()

    def _highlight_all(self):
        term = self._input.text()
        if not term:
            self._match_label.setText("")
            self._target.setExtraSelections([])
            return

        fmt = QTextCharFormat()
        fmt.setBackground(QColor(Colors.HIGHLIGHT))

        selections = []
        doc    = self._target.document()
        cursor = QTextCursor(doc)
        count  = 0
        while True:
            cursor = doc.find(term, cursor)   # no flags = forward, case-insensitive
            if cursor.isNull():
                break
            sel = QTextEdit.ExtraSelection()
            sel.cursor = cursor
            sel.format  = fmt
            selections.append(sel)
            count += 1

        self._target.setExtraSelections(selections)
        self._match_label.setText(f"{count} match{'es' if count != 1 else ''}")

    def _find_next(self):
        term = self._input.text()
        if not term:
            return
        if not self._target.find(term):
            cur = self._target.textCursor()
            cur.movePosition(QTextCursor.MoveOperation.Start)
            self._target.setTextCursor(cur)
            self._target.find(term)

    def _find_prev(self):
        term = self._input.text()
        if not term:
            return
        if not self._target.find(term, QTextDocument.FindFlag.FindBackward):
            cur = self._target.textCursor()
            cur.movePosition(QTextCursor.MoveOperation.End)
            self._target.setTextCursor(cur)
            self._target.find(term, QTextDocument.FindFlag.FindBackward)
