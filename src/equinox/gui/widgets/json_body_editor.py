"""
JSON body editor with:
- Syntax highlighting via the shared JsonHighlighter
- Auto-close brackets/quotes, smart wrapping, skip-over on retype
- Auto-indent and smart bracket splitting on Enter
- Shift-indent / dedent via Tab / Shift+Tab
- Auto-format JSON (Ctrl+Shift+F)
- Line comment toggle (Ctrl+/)
- Line numbers
- Structural bracket matching ({}, [], ())
"""
import json as _json
import logging
from collections.abc import Iterator

from equinox.gui.syntax_highlighter import JsonHighlighter
from PyQt6.QtCore import QRect
from PyQt6.QtCore import QSize
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtGui import QPainter
from PyQt6.QtGui import QPaintEvent
from PyQt6.QtGui import QResizeEvent
from PyQt6.QtGui import QTextBlock
from PyQt6.QtGui import QTextCharFormat
from PyQt6.QtGui import QTextCursor
from PyQt6.QtGui import QTextDocument
from PyQt6.QtWidgets import QPlainTextEdit
from PyQt6.QtWidgets import QTextEdit
from PyQt6.QtWidgets import QWidget

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

# Maximum document size (characters) allowed for JSON auto-format.
# Prevents blocking the UI thread on huge payloads.
_MAX_FORMAT_CHARS: int = 500_000

# Maximum characters scanned in each direction during bracket matching.
# Without a limit, large documents freeze the UI on every cursor move.
_MAX_BRACKET_SEARCH: int = 10_000

# Auto-close pairs: typing an opener inserts its closer automatically.
# Quotes are included because they act as self-closing pairs in JSON.
_AUTO_CLOSE_PAIRS: dict[str, str] = {"{": "}", "[": "]", '"': '"', "(": ")"}

# Structural bracket pairs used *only* for visual matching in the gutter.
# Quotes are intentionally excluded — they do not nest like brackets do.
_BRACKET_PAIRS: dict[str, str] = {"{": "}", "[": "]", "(": ")"}
_CLOSE_BRACKETS: frozenset[str] = frozenset(_BRACKET_PAIRS.values())

# All characters that may be skipped over when retyped next to an
# auto-inserted closer (includes the closing quote for self-pairs).
_CLOSE_CHARS: frozenset[str] = frozenset(_AUTO_CLOSE_PAIRS.values())

# Line-comment marker used by the toggle-comment feature (JSON5 / relaxed).
_LINE_COMMENT: str = "//"


# ---------------------------------------------------------------------------
# Line Number Area
# ---------------------------------------------------------------------------


class LineNumberArea(QWidget):
    """Gutter widget that renders per-line numbers beside a *JsonBodyEditor*."""

    def __init__(self, editor: "JsonBodyEditor") -> None:
        super().__init__(editor)
        self._editor = editor  # private — callers should not depend on this ref

    def sizeHint(self) -> QSize:
        return QSize(self._editor.line_number_area_width(), 0)

    def paintEvent(self, event: QPaintEvent | None) -> None:
        if event is None:
            return
        painter = QPainter(self)
        painter.fillRect(event.rect(), QColor(240, 240, 240))

        block = self._editor.firstVisibleBlock()
        block_number = block.blockNumber()
        top = (
            self._editor.blockBoundingGeometry(block).translated(self._editor.contentOffset()).top()
        )
        bottom = top + self._editor.blockBoundingRect(block).height()

        font = self._editor.font()
        font.setPointSize(font.pointSize() - 1)
        painter.setFont(font)
        painter.setPen(QColor(128, 128, 128))

        clip = event.rect()
        while block.isValid() and top <= clip.bottom():
            if block.isVisible() and bottom >= clip.top():
                painter.drawText(
                    0,
                    int(top),
                    self._editor.line_number_area_width() - 4,
                    int(self._editor.blockBoundingRect(block).height()),
                    Qt.AlignmentFlag.AlignRight,
                    str(block_number + 1),
                )
            block = block.next()
            top = bottom
            bottom = top + self._editor.blockBoundingRect(block).height()
            block_number += 1


# ---------------------------------------------------------------------------
# JsonBodyEditor
# ---------------------------------------------------------------------------


class JsonBodyEditor(QPlainTextEdit):
    """JSON-friendly plain-text editor.

    Features
    --------
    - Auto-close brackets / quotes with skip-over on retype.
    - Smart wrapping: select text then type an opener to wrap it.
    - Auto-indent preserving current depth; bracket splitting on Enter.
    - Shift-indent / dedent (Tab / Shift+Tab).
    - Auto-format JSON with Ctrl+Shift+F (≤ 500 KB documents).
    - Line-comment toggle with Ctrl+/.
    - Line-number gutter.
    - Structural bracket matching (``{}``, ``[]``, ``()``).
    - Syntax highlighting via ``JsonHighlighter``.
    """

    _INDENT_SIZE: int = 4

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        # Line numbers
        self._line_number_area = LineNumberArea(self)
        self.blockCountChanged.connect(self._update_line_number_area_width)
        self.updateRequest.connect(self._update_line_number_area)
        self._update_line_number_area_width(0)

        # Syntax highlighting
        doc = self.document()
        if doc is None:
            raise RuntimeError("JsonBodyEditor requires a valid QTextDocument")
        self._highlighter = JsonHighlighter(doc)

        # Bracket matching
        self.cursorPositionChanged.connect(self._highlight_matching_bracket)

    @property
    def line_number_area(self) -> LineNumberArea:
        """Read-only access to the line-number gutter widget."""
        return self._line_number_area

    # ------------------------------------------------------------------
    # Line-number gutter
    # ------------------------------------------------------------------

    def line_number_area_width(self) -> int:
        """Return the pixel width required to display all line numbers."""
        digits = len(str(self.blockCount())) + 1
        # Use actual font metrics instead of a hardcoded value so the gutter
        # stays correct on HiDPI displays and with non-default font sizes.
        char_width = self.fontMetrics().horizontalAdvance("9")
        return 4 + char_width * digits

    def _update_line_number_area_width(self, _: int) -> None:
        self.setViewportMargins(self.line_number_area_width(), 0, 0, 0)

    def _update_line_number_area(self, rect: QRect, dy: int) -> None:
        if dy:
            self._line_number_area.scroll(0, dy)
        else:
            self._line_number_area.update(
                0, rect.y(), self._line_number_area.width(), rect.height(),
            )
        viewport = self.viewport()
        if viewport is not None and rect.contains(viewport.rect()):
            self._update_line_number_area_width(0)

    def resizeEvent(self, event: QResizeEvent | None) -> None:
        super().resizeEvent(event)
        cr = self.contentsRect()
        self._line_number_area.setGeometry(
            cr.left(), cr.top(), self.line_number_area_width(), cr.height(),
        )

    # ------------------------------------------------------------------
    # Bracket matching
    # ------------------------------------------------------------------

    def _highlight_matching_bracket(self) -> None:
        """Highlight the bracket under the cursor and its structural match."""
        cursor = self.textCursor()
        pos = cursor.position()
        doc = self.document()
        if doc is None:
            return

        self.setExtraSelections([])

        if pos <= 0:
            return

        # O(1) per character via characterAt() — avoids copying the whole doc.
        char = doc.characterAt(pos - 1)
        match_pos: int | None = None

        if char in _BRACKET_PAIRS:
            match_pos = self._find_matching_forward(doc, pos - 1, char, _BRACKET_PAIRS[char])
        elif char in _CLOSE_BRACKETS:
            opener = next((k for k, v in _BRACKET_PAIRS.items() if v == char), None)
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

    def _find_matching_forward(
        self, doc: QTextDocument, start: int, open_char: str, close_char: str,
    ) -> int | None:
        """Return the position of the closing bracket matching *open_char* at *start*."""
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

    def _find_matching_backward(
        self, doc: QTextDocument, start: int, open_char: str, close_char: str,
    ) -> int | None:
        """Return the position of the opening bracket matching *close_char* at *start*."""
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

    def _make_selection(self, pos: int, fmt: QTextCharFormat) -> QTextEdit.ExtraSelection | None:
        """Return a single-character ``ExtraSelection`` at *pos*, or ``None`` if out of range."""
        doc = self.document()
        if doc is None:
            return None
        # characterCount() includes Qt's implicit trailing newline, so valid
        # user-character positions are in [0, characterCount() - 2].
        if pos < 0 or pos >= doc.characterCount() - 1:
            return None
        cursor = QTextCursor(doc)
        cursor.setPosition(pos)
        cursor.movePosition(
            QTextCursor.MoveOperation.NextCharacter, QTextCursor.MoveMode.KeepAnchor,
        )
        sel = QTextEdit.ExtraSelection()
        sel.cursor = cursor
        sel.format = fmt
        return sel

    # ------------------------------------------------------------------
    # Key event dispatch
    # ------------------------------------------------------------------

    def keyPressEvent(self, event: QKeyEvent | None) -> None:
        if event is None:
            return
        key = event.key()
        mods = event.modifiers()
        cursor = self.textCursor()
        text = event.text()

        # Auto-format JSON (Ctrl+Shift+F)
        if key == Qt.Key.Key_F and mods == (
            Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier
        ):
            self._auto_format_json()
            return

        # Toggle line comment (Ctrl+/)
        if key == Qt.Key.Key_Slash and mods == Qt.KeyboardModifier.ControlModifier:
            self._toggle_comment()
            return

        # Smart wrapping: selection + opener → surround with the matching pair
        if cursor.hasSelection() and text in _AUTO_CLOSE_PAIRS:
            self._wrap_selection(text, cursor)
            return

        # Tab / Shift+Tab
        if key == Qt.Key.Key_Tab:
            if mods & Qt.KeyboardModifier.ShiftModifier:
                self._shift_selection(indent=False)
            else:
                self._indent_or_insert_spaces(cursor)
            return

        # Smart Enter (auto-indent + bracket splitting)
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not mods:
            self._handle_enter(cursor)
            return

        # Backspace: delete the matched pair when cursor sits between opener+closer
        if key == Qt.Key.Key_Backspace and not mods:
            if self._handle_backspace(cursor):
                return

        # Skip-over: re-typing an auto-inserted closer moves the cursor past it.
        # This must run BEFORE auto-close so that same-character pairs (quotes)
        # are handled correctly without a special case in _handle_auto_close.
        if text in _CLOSE_CHARS:
            if self._handle_skip_close(text, cursor):
                return

        # Auto-close: insert opener + closer and position cursor between them
        if text in _AUTO_CLOSE_PAIRS:
            self._handle_auto_close(text, cursor)
            return

        super().keyPressEvent(event)

    # ------------------------------------------------------------------
    # Editing helpers
    # ------------------------------------------------------------------

    def _wrap_selection(self, opener: str, cursor: QTextCursor) -> None:
        """Surround the selected text with *opener* and its matching closer."""
        closer = _AUTO_CLOSE_PAIRS[opener]
        selected = cursor.selectedText()
        cursor.beginEditBlock()
        try:
            cursor.insertText(opener + selected + closer)
            cursor.movePosition(QTextCursor.MoveOperation.PreviousCharacter)
        finally:
            cursor.endEditBlock()
        self.setTextCursor(cursor)

    def _indent_or_insert_spaces(self, cursor: QTextCursor) -> None:
        """Tab key: indent the selection or insert spaces at the cursor."""
        if cursor.hasSelection():
            self._shift_selection(indent=True)
        else:
            cursor.insertText(" " * self._INDENT_SIZE)

    def _shift_selection(self, *, indent: bool) -> None:
        """Indent or dedent every line covered by the current selection by one level."""
        cursor = self.textCursor()
        spaces = " " * self._INDENT_SIZE
        cursor.beginEditBlock()
        try:
            for block in self._iter_selected_blocks(cursor):
                if not block.text().strip():
                    continue
                c = QTextCursor(block)
                if indent:
                    c.insertText(spaces)
                elif block.text().startswith(spaces):
                    for _ in range(self._INDENT_SIZE):
                        c.deleteChar()
        finally:
            cursor.endEditBlock()

    def _handle_enter(self, cursor: QTextCursor) -> None:
        """Insert a newline with smart indentation.

        When the cursor sits between an opening bracket and its corresponding
        closer (e.g. ``{|}``), the closer is pushed to its own indented line so
        the result looks like::

            {
                |
            }
        """
        block_text = cursor.block().text()
        indent = len(block_text) - len(block_text.lstrip())
        # Examine only the text *before* the cursor so that pressing Enter in
        # the middle of a line (e.g. ``{"key": "val"|}``) does not add extra
        # indentation just because the block starts with ``{``.
        col = cursor.positionInBlock()
        text_before = block_text[:col].rstrip()
        char_after = self._adjacent_char(cursor, forward=True)

        opens_block = text_before.endswith(("{", "["))
        closes_block = char_after in ("}", "]")
        spaces = " " * indent

        if opens_block and closes_block:
            # Smart split: inner indented line + closing bracket on its own line.
            inner = spaces + " " * self._INDENT_SIZE
            cursor.beginEditBlock()
            try:
                cursor.insertText(f"\n{inner}\n{spaces}")
                cursor.movePosition(QTextCursor.MoveOperation.Up)
                cursor.movePosition(QTextCursor.MoveOperation.EndOfLine)
            finally:
                cursor.endEditBlock()
            self.setTextCursor(cursor)
        else:
            extra = self._INDENT_SIZE if opens_block else 0
            cursor.insertText(f"\n{spaces}{' ' * extra}")

    def _handle_backspace(self, cursor: QTextCursor) -> bool:
        """Delete both characters of a matched pair when the cursor is between them."""
        before = self._adjacent_char(cursor, forward=False)
        after = self._adjacent_char(cursor, forward=True)
        if before and after and _AUTO_CLOSE_PAIRS.get(before) == after:
            cursor.deletePreviousChar()
            cursor.deleteChar()
            return True
        return False

    def _handle_auto_close(self, ch: str, cursor: QTextCursor) -> None:
        """Insert *ch* followed by its closer, leaving the cursor between them."""
        closer = _AUTO_CLOSE_PAIRS[ch]
        cursor.insertText(ch + closer)
        cursor.movePosition(QTextCursor.MoveOperation.PreviousCharacter)
        self.setTextCursor(cursor)

    def _handle_skip_close(self, ch: str, cursor: QTextCursor) -> bool:
        """Move the cursor past the next character when it already equals *ch*."""
        if self._adjacent_char(cursor, forward=True) == ch:
            cursor.movePosition(QTextCursor.MoveOperation.NextCharacter)
            self.setTextCursor(cursor)
            return True
        return False

    # ------------------------------------------------------------------
    # Line-comment toggle
    # ------------------------------------------------------------------

    def _toggle_comment(self) -> None:
        """Comment or uncomment every line covered by the current selection.

        All lines are uncommented only when *every* selected line already begins
        with a comment marker; otherwise all lines are commented.
        """
        cursor = self.textCursor()
        blocks = list(self._iter_selected_blocks(cursor))
        all_commented = all(b.text().lstrip().startswith(_LINE_COMMENT) for b in blocks)
        cursor.beginEditBlock()
        try:
            for block in blocks:
                if all_commented:
                    self._uncomment_line(block)
                else:
                    self._comment_line(block)
        finally:
            cursor.endEditBlock()

    def _comment_line(self, block: QTextBlock) -> None:
        """Prepend a ``// `` comment marker at the first non-whitespace column."""
        indent = len(block.text()) - len(block.text().lstrip())
        cursor = QTextCursor(block)
        cursor.movePosition(
            QTextCursor.MoveOperation.Right, QTextCursor.MoveMode.MoveAnchor, indent,
        )
        cursor.insertText(_LINE_COMMENT + " ")

    def _uncomment_line(self, block: QTextBlock) -> None:
        """Remove a leading ``//`` or ``// `` comment marker from *block*."""
        text = block.text()
        indent = len(text) - len(text.lstrip())
        stripped = text[indent:]

        if stripped.startswith(_LINE_COMMENT + " "):
            remove = len(_LINE_COMMENT) + 1
        elif stripped.startswith(_LINE_COMMENT):
            remove = len(_LINE_COMMENT)
        else:
            return

        cursor = QTextCursor(block)
        cursor.movePosition(
            QTextCursor.MoveOperation.Right, QTextCursor.MoveMode.MoveAnchor, indent,
        )
        for _ in range(remove):
            cursor.deleteChar()

    # ------------------------------------------------------------------
    # JSON auto-format
    # ------------------------------------------------------------------

    def _auto_format_json(self) -> None:
        """Reformat the document as pretty-printed JSON (Ctrl+Shift+F).

        Silently skips documents larger than ``_MAX_FORMAT_CHARS`` characters
        to avoid blocking the UI thread on huge payloads.
        """
        text = self.toPlainText().strip()
        if not text:
            return
        if len(text) > _MAX_FORMAT_CHARS:
            logger.warning(
                "JSON auto-format skipped — document too large (%d chars, limit %d)",
                len(text),
                _MAX_FORMAT_CHARS,
            )
            return
        try:
            parsed = _json.loads(text)
        except _json.JSONDecodeError as exc:
            logger.debug("JSON auto-format skipped — parse error: %s", exc)
            return
        formatted = _json.dumps(parsed, indent=self._INDENT_SIZE, ensure_ascii=False)
        # Preserve the vertical scroll position; setPlainText() resets the
        # viewport to the top, which is disorienting for large documents.
        scrollbar = self.verticalScrollBar()
        saved_scroll = scrollbar.value() if scrollbar is not None else 0
        self.setPlainText(formatted)
        if scrollbar is not None:
            scrollbar.setValue(saved_scroll)

    # ------------------------------------------------------------------
    # Private utilities
    # ------------------------------------------------------------------

    def _iter_selected_blocks(self, cursor: QTextCursor) -> Iterator[QTextBlock]:
        """Yield each ``QTextBlock`` covered by *cursor*'s selection.

        Falls back to just the current block when there is no selection.
        """
        doc = self.document()
        if doc is None:
            yield cursor.block()
            return
        if cursor.hasSelection():
            start = doc.findBlock(cursor.selectionStart())
            end_block = doc.findBlock(cursor.selectionEnd() - 1)
        else:
            start = end_block = cursor.block()
        end_num = end_block.blockNumber()
        block = start
        while block.isValid() and block.blockNumber() <= end_num:
            yield block
            block = block.next()

    @staticmethod
    def _adjacent_char(cursor: QTextCursor, *, forward: bool) -> str:
        """Return the single character immediately before or after *cursor*.

        Parameters
        ----------
        cursor:
            The reference cursor (not mutated).
        forward:
            ``True`` to read the character *after* the cursor;
            ``False`` for the character *before* it.
        """
        c = QTextCursor(cursor)
        op = (
            QTextCursor.MoveOperation.NextCharacter
            if forward
            else QTextCursor.MoveOperation.PreviousCharacter
        )
        c.movePosition(op, QTextCursor.MoveMode.KeepAnchor)
        return c.selectedText()
