"""
Refactored JSON body editor:
- Uses unified syntax highlighter (JsonHighlighter)
- Cleaner structure, DRY, better separation of concerns
"""

import json as _json
import logging

from PyQt6.QtWidgets import QWidget, QPlainTextEdit, QTextEdit
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QTextCursor, QPainter, QColor, QTextCharFormat

from equinox.gui.syntax_highlighter import JsonHighlighter

logger = logging.getLogger(__name__)

# Maximum number of characters searched in each direction when looking for a
# matching bracket.  Without a limit, bracket matching on a large document can
# scan the entire file on every cursor movement and freeze the UI.
_MAX_BRACKET_SEARCH = 10_000


# ---------------------------------------------------------------------------
# Line Number Area
# ---------------------------------------------------------------------------

class LineNumberArea(QWidget):
    """Line number display for JsonBodyEditor."""

    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor

    def sizeHint(self) -> QSize:
        return QSize(self.editor.line_number_area_width(), 0)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(event.rect(), QColor(240, 240, 240))

        block = self.editor.firstVisibleBlock()
        block_number = block.blockNumber()
        top = self.editor.blockBoundingGeometry(block).translated(
            self.editor.contentOffset()
        ).top()
        bottom = top + self.editor.blockBoundingRect(block).height()

        font = self.editor.font()
        font.setPointSize(font.pointSize() - 1)
        painter.setFont(font)
        painter.setPen(QColor(128, 128, 128))

        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                painter.drawText(
                    0,
                    int(top),
                    self.editor.line_number_area_width() - 4,
                    int(self.editor.blockBoundingRect(block).height()),
                    Qt.AlignmentFlag.AlignRight,
                    str(block_number + 1),
                )

            block = block.next()
            top = bottom
            bottom = top + self.editor.blockBoundingRect(block).height()
            block_number += 1


# ---------------------------------------------------------------------------
# JsonBodyEditor
# ---------------------------------------------------------------------------

_OPEN_CLOSE = {"{": "}", "[": "]", '"': '"', "(": ")"}
_CLOSE_CHARS = set(_OPEN_CLOSE.values())


class JsonBodyEditor(QPlainTextEdit):
    """JSON‑friendly editor with:
    - Auto‑close brackets/quotes
    - Auto‑indent
    - Smart wrapping
    - Auto‑format JSON (Ctrl+Shift+F)
    - Comment toggling (Ctrl+/)
    - Line numbers
    - Bracket matching
    - Uses unified JsonHighlighter
    """

    _INDENT_SIZE = 4

    def __init__(self, parent=None):
        super().__init__(parent)

        # Line numbers
        self.line_number_area = LineNumberArea(self)
        self.blockCountChanged.connect(self._update_line_number_area_width)
        self.updateRequest.connect(self._update_line_number_area)
        self._update_line_number_area_width(0)

        # Syntax highlighting (NEW)
        self._highlighter = JsonHighlighter(self.document())

        # Bracket matching
        self.cursorPositionChanged.connect(self._highlight_matching_bracket)

    # ------------------------------------------------------------------
    # Line numbers
    # ------------------------------------------------------------------

    def line_number_area_width(self) -> int:
        digits = len(str(self.document().blockCount())) + 1
        # Use actual font metrics instead of a hardcoded 8 px per digit so
        # the gutter stays correct on HiDPI displays and with larger fonts.
        char_width = self.fontMetrics().horizontalAdvance("9")
        return 4 + char_width * digits

    def _update_line_number_area_width(self, _):
        self.setViewportMargins(self.line_number_area_width(), 0, 0, 0)

    def _update_line_number_area(self, rect, dy):
        if dy:
            self.line_number_area.scroll(0, dy)
        else:
            self.line_number_area.update(
                0, rect.y(), self.line_number_area.width(), rect.height()
            )

        if rect.contains(self.viewport().rect()):
            self._update_line_number_area_width(0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        cr = self.contentsRect()
        self.line_number_area.setGeometry(
            cr.left(), cr.top(), self.line_number_area_width(), cr.height()
        )

    # ------------------------------------------------------------------
    # Bracket matching
    # ------------------------------------------------------------------

    def _highlight_matching_bracket(self):
        cursor = self.textCursor()
        pos = cursor.position()
        doc = self.document()

        self.setExtraSelections([])

        if pos <= 0:
            return

        # Use document.characterAt() — O(1) per character — instead of
        # toPlainText() which copies the entire document into a Python str.
        char = doc.characterAt(pos - 1)
        match_pos = None

        if char in _OPEN_CLOSE:
            match_pos = self._find_matching_forward(doc, pos - 1, char, _OPEN_CLOSE[char])
        elif char in _CLOSE_CHARS:
            opener = next((k for k, v in _OPEN_CLOSE.items() if v == char), None)
            if opener:
                match_pos = self._find_matching_backward(doc, pos - 1, opener, char)

        if match_pos is not None:
            fmt = QTextCharFormat()
            fmt.setBackground(QColor(200, 200, 0, 100))
            sels = []
            for p in (pos - 1, match_pos):
                sel = self._make_selection(p, fmt)
                if sel is not None:
                    sels.append(sel)
            if sels:
                self.setExtraSelections(sels)

    def _find_matching_forward(self, doc, start, open_char, close_char):
        depth = 1
        limit = min(start + _MAX_BRACKET_SEARCH, doc.characterCount())
        for i in range(start + 1, limit):
            ch = doc.characterAt(i)
            if ch == open_char:
                depth += 1
            elif ch == close_char:
                depth -= 1
                if depth == 0:
                    return i
        return None

    def _find_matching_backward(self, doc, start, open_char, close_char):
        depth = 1
        limit = max(start - _MAX_BRACKET_SEARCH, -1)
        for i in range(start - 1, limit, -1):
            ch = doc.characterAt(i)
            if ch == close_char:
                depth += 1
            elif ch == open_char:
                depth -= 1
                if depth == 0:
                    return i
        return None

    def _make_selection(self, pos, fmt):
        doc = self.document()
        # characterCount() always includes Qt's implicit trailing newline,
        # so valid user-character positions are in [0, characterCount() - 2].
        if pos < 0 or pos >= doc.characterCount() - 1:
            return None
        cursor = QTextCursor(doc)
        cursor.setPosition(pos)
        cursor.movePosition(QTextCursor.MoveOperation.NextCharacter, QTextCursor.MoveMode.KeepAnchor)
        sel = QTextEdit.ExtraSelection()
        sel.cursor = cursor
        sel.format = fmt
        return sel

    # ------------------------------------------------------------------
    # Key handling
    # ------------------------------------------------------------------

    def keyPressEvent(self, event):
        key = event.key()
        mods = event.modifiers()
        cursor = self.textCursor()

        # Auto‑format JSON
        if key == Qt.Key.Key_F and mods == (
            Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier
        ):
            self._auto_format_json()
            return

        # Toggle comment
        if key == Qt.Key.Key_Slash and mods == Qt.KeyboardModifier.ControlModifier:
            self._toggle_comment()
            return

        # Smart wrapping
        if cursor.hasSelection() and event.text() in _OPEN_CLOSE:
            self._wrap_selection(event.text(), cursor)
            return

        # Tab / Shift+Tab
        if key == Qt.Key.Key_Tab:
            if mods & Qt.KeyboardModifier.ShiftModifier:
                self._dedent_selection()
            else:
                self._indent_selection_or_insert_spaces(cursor)
            return

        # Enter
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not mods:
            self._handle_enter(cursor)
            return

        # Backspace
        if key == Qt.Key.Key_Backspace and not mods:
            if self._handle_backspace(cursor):
                return

        # Auto‑close
        if event.text() in _OPEN_CLOSE:
            if self._handle_auto_close(event.text(), cursor):
                return

        # Skip‑over
        if event.text() in _CLOSE_CHARS:
            if self._handle_skip_close(event.text(), cursor):
                return

        super().keyPressEvent(event)

    # ------------------------------------------------------------------
    # Key helpers
    # ------------------------------------------------------------------

    def _wrap_selection(self, opener, cursor):
        closer = _OPEN_CLOSE[opener]
        selected = cursor.selectedText()
        # Single undo step for the whole wrap operation.
        cursor.beginEditBlock()
        try:
            cursor.insertText(opener + selected + closer)
            cursor.movePosition(QTextCursor.MoveOperation.PreviousCharacter)
        finally:
            cursor.endEditBlock()
        self.setTextCursor(cursor)

    def _indent_selection_or_insert_spaces(self, cursor):
        if cursor.hasSelection():
            self._indent_selection()
        else:
            cursor.insertText(" " * self._INDENT_SIZE)

    def _indent_selection(self):
        outer = self.textCursor()
        start = self.document().findBlock(outer.selectionStart())
        end = self.document().findBlock(outer.selectionEnd() - 1)

        # Single undo step for the entire indent operation.
        outer.beginEditBlock()
        try:
            block = start
            while block.isValid() and block.blockNumber() <= end.blockNumber():
                if block.text().strip():
                    c = QTextCursor(block)
                    c.insertText(" " * self._INDENT_SIZE)
                block = block.next()
        finally:
            outer.endEditBlock()

    def _dedent_selection(self):
        outer = self.textCursor()
        start = self.document().findBlock(outer.selectionStart())
        end = self.document().findBlock(outer.selectionEnd() - 1)

        # Single undo step for the entire dedent operation.
        outer.beginEditBlock()
        try:
            block = start
            while block.isValid() and block.blockNumber() <= end.blockNumber():
                text = block.text()
                if text.startswith(" " * self._INDENT_SIZE):
                    c = QTextCursor(block)
                    for _ in range(self._INDENT_SIZE):
                        c.deleteChar()
                block = block.next()
        finally:
            outer.endEditBlock()

    def _handle_enter(self, cursor):
        block_text = cursor.block().text()
        indent = len(block_text) - len(block_text.lstrip())
        # Check the text *before the cursor position within the line*, not the
        # entire block text.  Without this, pressing Enter in the middle of
        # "{"key": "val"}" incorrectly adds extra indentation.
        col = cursor.positionInBlock()
        text_before_cursor = block_text[:col].rstrip()
        extra = self._INDENT_SIZE if text_before_cursor.endswith(("{", "[")) else 0
        cursor.insertText("\n" + " " * (indent + extra))

    def _handle_backspace(self, cursor):
        before = self._char_before(cursor)
        after = self._char_after(cursor)
        if before and after and _OPEN_CLOSE.get(before) == after:
            cursor.deletePreviousChar()
            cursor.deleteChar()
            return True
        return False

    def _handle_auto_close(self, ch, cursor):
        closer = _OPEN_CLOSE[ch]
        if ch == '"' and self._char_after(cursor) == '"':
            cursor.movePosition(QTextCursor.MoveOperation.NextCharacter)
            self.setTextCursor(cursor)
            return True
        cursor.insertText(ch + closer)
        cursor.movePosition(QTextCursor.MoveOperation.PreviousCharacter)
        self.setTextCursor(cursor)
        return True

    def _handle_skip_close(self, ch, cursor):
        if self._char_after(cursor) == ch:
            cursor.movePosition(QTextCursor.MoveOperation.NextCharacter)
            self.setTextCursor(cursor)
            return True
        return False

    # ------------------------------------------------------------------
    # Comment toggling
    # ------------------------------------------------------------------

    def _toggle_comment(self):
        cursor = self.textCursor()
        if cursor.hasSelection():
            self._toggle_comment_lines(cursor)
        else:
            block = cursor.block()
            self._toggle_comment_line(block)

    def _toggle_comment_lines(self, cursor):
        start = self.document().findBlock(cursor.selectionStart())
        end = self.document().findBlock(cursor.selectionEnd() - 1)

        # Determine mode: comment or uncomment
        block = start
        uncomment = True
        while block.isValid() and block.blockNumber() <= end.blockNumber():
            if not block.text().lstrip().startswith("//"):
                uncomment = False
                break
            block = block.next()

        # Group all per-line edits into a single undo step so one Ctrl+Z
        # reverses the entire toggle, not one line at a time.
        cursor.beginEditBlock()
        try:
            block = start
            while block.isValid() and block.blockNumber() <= end.blockNumber():
                if uncomment:
                    self._uncomment_line(block)
                else:
                    self._comment_line(block)
                block = block.next()
        finally:
            cursor.endEditBlock()

    def _toggle_comment_line(self, block):
        text = block.text().lstrip()
        if text.startswith("//"):
            self._uncomment_line(block)
        else:
            self._comment_line(block)

    def _comment_line(self, block):
        cursor = QTextCursor(block)
        indent = len(block.text()) - len(block.text().lstrip())
        cursor.movePosition(QTextCursor.MoveOperation.StartOfBlock)
        cursor.movePosition(QTextCursor.MoveOperation.Right, QTextCursor.MoveMode.MoveAnchor, indent)
        cursor.insertText("// ")

    def _uncomment_line(self, block):
        text = block.text()
        indent = len(text) - len(text.lstrip())
        stripped = text[indent:]

        if stripped.startswith("// "):
            remove = 3
        elif stripped.startswith("//"):
            remove = 2
        else:
            return

        cursor = QTextCursor(block)
        cursor.movePosition(QTextCursor.MoveOperation.StartOfBlock)
        cursor.movePosition(QTextCursor.MoveOperation.Right, QTextCursor.MoveMode.MoveAnchor, indent)
        for _ in range(remove):
            cursor.deleteChar()

    # ------------------------------------------------------------------
    # JSON formatting
    # ------------------------------------------------------------------

    def _auto_format_json(self):
        text = self.toPlainText().strip()
        if not text:
            return
        try:
            parsed = _json.loads(text)
        except _json.JSONDecodeError as exc:
            logger.debug("JSON auto-format skipped — parse error: %s", exc)
            return
        formatted = _json.dumps(parsed, indent=self._INDENT_SIZE, ensure_ascii=False)
        # Preserve the vertical scroll position; setPlainText() resets the
        # viewport to the top which is disorienting for large documents.
        scrollbar = self.verticalScrollBar()
        saved_scroll = scrollbar.value()
        self.setPlainText(formatted)
        scrollbar.setValue(saved_scroll)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _char_before(cursor):
        c = QTextCursor(cursor)
        c.movePosition(QTextCursor.MoveOperation.PreviousCharacter,
                       QTextCursor.MoveMode.KeepAnchor)
        return c.selectedText()

    @staticmethod
    def _char_after(cursor):
        c = QTextCursor(cursor)
        c.movePosition(QTextCursor.MoveOperation.NextCharacter,
                       QTextCursor.MoveMode.KeepAnchor)
        return c.selectedText()
