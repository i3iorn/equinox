from PyQt6.QtGui import QTextCharFormat, QColor, QTextCursor, QTextDocument
from PyQt6.QtWidgets import QWidget, QTextEdit, QHBoxLayout, QLineEdit, QLabel, QToolButton

from equinox.gui.theme import Colors


class SearchBar(QWidget):
    """Inline find-bar for a QTextEdit — shown/hidden with Ctrl+F."""

    def __init__(self, target: QTextEdit, parent=None):
        super().__init__(parent)
        self._target = target
        self._matches: list = []   # list[QTextCursor] — populated by _collect_matches
        self._current_idx: int = -1
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
        self._matches = []
        self._current_idx = -1
        self._target.setExtraSelections([])
        self._target.setFocus()

    # ── internal helpers ──────────────────────────────────────────────

    def _on_text_changed(self, _text):
        self._collect_matches()
        self._current_idx = 0 if self._matches else -1
        self._apply_highlights()

    def _collect_matches(self):
        """Scan the document and store every matching cursor in self._matches."""
        term = self._input.text()
        if not term:
            self._matches = []
            self._match_label.setText("")
            return

        doc = self._target.document()
        cursor = QTextCursor(doc)
        matches = []
        while True:
            cursor = doc.find(term, cursor)   # forward, case-sensitive
            if cursor.isNull():
                break
            matches.append(QTextCursor(cursor))   # copy the cursor

        self._matches = matches
        count = len(matches)
        self._match_label.setText(f"{count} match{'es' if count != 1 else ''}")

    def _apply_highlights(self):
        """Repaint extra selections: current match in orange, others in yellow."""
        if not self._matches:
            self._target.setExtraSelections([])
            return

        dim_fmt = QTextCharFormat()
        dim_fmt.setBackground(QColor(Colors.HIGHLIGHT))

        cur_fmt = QTextCharFormat()
        cur_fmt.setBackground(QColor("#e8a030"))   # orange — visible on light & dark themes

        selections = []
        for i, cur in enumerate(self._matches):
            sel = QTextEdit.ExtraSelection()
            sel.cursor = cur
            sel.format = cur_fmt if i == self._current_idx else dim_fmt
            selections.append(sel)

        self._target.setExtraSelections(selections)

        # Scroll current match into view without giving the editor focus
        if 0 <= self._current_idx < len(self._matches):
            self._target.setTextCursor(self._matches[self._current_idx])
            self._target.ensureCursorVisible()

    def _update_label(self):
        count = len(self._matches)
        if count == 0:
            self._match_label.setText("no matches")
        else:
            self._match_label.setText(
                f"{self._current_idx + 1} / {count} match{'es' if count != 1 else ''}"
            )

    # ── navigation ────────────────────────────────────────────────────

    def _find_next(self):
        if not self._matches:
            return
        self._current_idx = (self._current_idx + 1) % len(self._matches)
        self._apply_highlights()
        self._update_label()

    def _find_prev(self):
        if not self._matches:
            return
        self._current_idx = (self._current_idx - 1) % len(self._matches)
        self._apply_highlights()
        self._update_label()
