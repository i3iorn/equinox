"""JSON body editor with bracket-matching, auto-indent, line numbers, and more."""

import json as _json
import re
from PyQt6.QtWidgets import QTextEdit, QWidget, QVBoxLayout, QPlainTextEdit
from PyQt6.QtCore import Qt, QRect, QSize
from PyQt6.QtGui import QTextCursor, QPainter, QColor, QFont, QTextFormat, QTextCharFormat

_OPEN_CLOSE = {"{": "}", "[": "]", '"': '"', "(": ")"}
_CLOSE_CHARS = set(_OPEN_CLOSE.values())


class LineNumberArea(QWidget):
    """Line number display for JSON editor."""
    
    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor
    
    def sizeHint(self) -> QSize:
        """Return width of line number area."""
        return QSize(self.editor.line_number_area_width(), 0)
    
    def paintEvent(self, event):
        """Paint line numbers."""
        painter = QPainter(self)
        painter.fillRect(event.rect(), QColor(240, 240, 240))
        
        block = self.editor.firstVisibleBlock()
        block_number = block.blockNumber()
        top = self.editor.blockBoundingGeometry(block).translated(self.editor.contentOffset()).top()
        bottom = top + self.editor.blockBoundingRect(block).height()
        
        font = self.editor.font()
        font.setPointSize(font.pointSize() - 1)
        painter.setFont(font)
        painter.setPen(QColor(128, 128, 128))
        
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                number = str(block_number + 1)
                painter.drawText(0, int(top), self.editor.line_number_area_width() - 4,
                               int(self.editor.blockBoundingRect(block).height()),
                               Qt.AlignmentFlag.AlignRight, number)
            
            block = block.next()
            top = bottom
            bottom = top + self.editor.blockBoundingRect(block).height()
            block_number += 1


class JsonBodyEditor(QTextEdit):
    """QTextEdit with JSON-friendly editing helpers:

    Features:
    - Auto-close ``{``, ``[``, ``(`` and ``"`` (inserts matching closer)
    - Smart ``"``-handling: skip over if already before a quote
    - Tab inserts 4 spaces (never hard tab)
    - Enter auto-indents with smart bracket handling
    - Backspace removes matching auto-close pairs
    - **NEW: Smart bracket/quote wrapping for selected text**
    - **NEW: Auto-format/beautify JSON (Ctrl+Shift+F)**
    - **NEW: Line numbers display**
    - **NEW: Bracket matching highlighting**
    - **NEW: Comment support (// and /* */)**
    """

    _INDENT_SIZE = 4

    def __init__(self, parent=None):
        super().__init__(parent)
        
        # Line number area
        self.line_number_area = LineNumberArea(self)
        self.blockCountChanged.connect(self._update_line_number_area_width)
        self.updateRequest.connect(self._update_line_number_area)
        self._update_line_number_area_width(0)
        
        # Visual settings
        self.setAcceptRichText(False)
        self._bracket_pairs = {}  # Cache bracket positions for highlighting
        self.cursorPositionChanged.connect(self._on_cursor_position_changed)

    def line_number_area_width(self) -> int:
        """Calculate width needed for line numbers."""
        digits = len(str(self.document().blockCount())) + 1
        return 4 + digits * 8

    def _update_line_number_area_width(self, _):
        """Update left margin to accommodate line numbers."""
        self.setViewportMargins(self.line_number_area_width(), 0, 0, 0)

    def _update_line_number_area(self, rect, dy):
        """Update line number area when viewport scrolls."""
        if dy:
            self.line_number_area.scroll(0, dy)
        else:
            self.line_number_area.update(0, rect.y(), self.line_number_area.width(), rect.height())
        
        if rect.contains(self.viewport().rect()):
            self._update_line_number_area_width(0)

    def resizeEvent(self, event):
        """Resize line number area with editor."""
        super().resizeEvent(event)
        cr = self.contentsRect()
        self.line_number_area.setGeometry(cr.left(), cr.top(),
                                         self.line_number_area_width(), cr.height())

    def _on_cursor_position_changed(self):
        """Highlight matching bracket when cursor moves."""
        self._highlight_matching_bracket()

    def _highlight_matching_bracket(self):
        """Find and highlight matching bracket/brace at cursor."""
        cursor = self.textCursor()
        text = self.toPlainText()
        pos = cursor.positionInBlock()
        
        # Get character at cursor
        block = cursor.block()
        block_text = block.text()
        
        if pos >= len(block_text):
            return
        
        char = block_text[pos] if pos < len(block_text) else ""
        
        # Find matching bracket
        matching_pos = None
        if char in _OPEN_CLOSE:
            matching_char = _OPEN_CLOSE[char]
            # Find closing bracket
            count = 1
            search_pos = cursor.position() + 1
            while search_pos < len(text) and count > 0:
                if text[search_pos] == char:
                    count += 1
                elif text[search_pos] == matching_char:
                    count -= 1
                    if count == 0:
                        matching_pos = search_pos
                        break
                search_pos += 1
        elif char in _CLOSE_CHARS:
            # Find opening bracket
            opening = None
            for k, v in _OPEN_CLOSE.items():
                if v == char:
                    opening = k
                    break
            if opening:
                count = 1
                search_pos = cursor.position() - 1
                while search_pos >= 0 and count > 0:
                    if text[search_pos] == char:
                        count += 1
                    elif text[search_pos] == opening:
                        count -= 1
                        if count == 0:
                            matching_pos = search_pos
                            break
                    search_pos -= 1
        
        # Clear previous highlights
        fmt = QTextCharFormat()
        cursor.select(QTextCursor.SelectionType.Document)
        cursor.setCharFormat(fmt)
        self.setTextCursor(cursor)

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        key = event.key()
        modifiers = event.modifiers()
        
        # Ctrl+Shift+F → auto-format JSON
        if key == Qt.Key.Key_F and modifiers == (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier):
            self._auto_format_json()
            return
        
        # Ctrl+/ → toggle comment
        if key == Qt.Key.Key_Slash and modifiers == Qt.KeyboardModifier.ControlModifier:
            self._toggle_comment()
            return
        
        cursor = self.textCursor()

        # Handle smart wrapping for selected text with brackets/quotes
        if self._handle_wrap_selection(event.text(), cursor):
            return

        if self._handle_tab(key, modifiers, cursor):
            return
        if self._handle_enter(key, modifiers, cursor):
            return
        if self._handle_backspace(key, modifiers, cursor):
            return
        if self._handle_auto_close(event.text(), cursor):
            return
        if self._handle_skip_close(event.text(), cursor):
            return

        super().keyPressEvent(event)

    # ── Key handlers ──────────────────────────────────────────────────

    def _handle_wrap_selection(self, text: str, cursor: QTextCursor) -> bool:
        """Wrap selected text with matching brackets/quotes."""
        if not cursor.hasSelection() or text not in _OPEN_CLOSE:
            return False
        
        # Get selected text
        selected = cursor.selectedText()
        closer = _OPEN_CLOSE[text]
        
        # Replace selection with wrapped text
        cursor.removeSelectedText()
        cursor.insertText(text + selected + closer)
        
        # Move cursor to just after selection
        cursor.movePosition(QTextCursor.MoveOperation.PreviousCharacter)
        self.setTextCursor(cursor)
        return True

    def _handle_enter(self, key: int, modifiers, cursor: QTextCursor) -> bool:
        """Enter / Return → auto-indent."""
        if key not in (Qt.Key.Key_Return, Qt.Key.Key_Enter) or modifiers:
            return False

        block_text = cursor.block().text()
        leading_indent = len(block_text) - len(block_text.lstrip())
        stripped = block_text.rstrip()
        extra = self._INDENT_SIZE if stripped.endswith(("{", "[")) else 0
        new_indent = " " * (leading_indent + extra)

        # If the character immediately after the cursor is a closer that
        # matches the opener at the end of the line, put the closer on its
        # own line at the original indent level (Postman/VSCode behaviour).
        char_after = self._char_after_cursor(cursor)
        if extra and char_after in ("}", "]"):
            cursor.insertText(f"\n{new_indent}\n{' ' * leading_indent}")
            # move cursor back up into the inner line
            new_cursor = self.textCursor()
            new_cursor.movePosition(QTextCursor.MoveOperation.Up)
            new_cursor.movePosition(QTextCursor.MoveOperation.EndOfLine)
            self.setTextCursor(new_cursor)
        else:
            cursor.insertText(f"\n{new_indent}")
        return True

    def _handle_backspace(self, key: int, modifiers, cursor: QTextCursor) -> bool:
        """Backspace → remove auto-close pair or dedent."""
        if key != Qt.Key.Key_Backspace or modifiers:
            return False
        
        if cursor.hasSelection():
            # Let default backspace handle selection deletion
            return False
        
        char_before = self._char_before_cursor(cursor)
        char_after = self._char_after_cursor(cursor)
        
        # Remove matching pair
        if char_before and char_after and _OPEN_CLOSE.get(char_before) == char_after:
            cursor.deletePreviousChar()
            cursor.deleteChar()
            return True
        
        # If at line start with spaces, dedent by INDENT_SIZE
        block_text = cursor.block().text()
        pos_in_block = cursor.positionInBlock()
        if pos_in_block > 0 and block_text[:pos_in_block].strip() == "":
            dedent = min(self._INDENT_SIZE, pos_in_block % self._INDENT_SIZE or self._INDENT_SIZE)
            for _ in range(dedent):
                cursor.deletePreviousChar()
            return True
        
        return False

    def _handle_auto_close(self, text: str, cursor: QTextCursor) -> bool:
        """Auto-close openers like ``{``, ``[``, ``"``."""
        if text not in _OPEN_CLOSE:
            return False
        closer = _OPEN_CLOSE[text]
        if text == '"' and self._char_after_cursor(cursor) == '"':
            # Already sitting before a closing quote — jump over it
            cursor.movePosition(QTextCursor.MoveOperation.NextCharacter)
            self.setTextCursor(cursor)
            return True
        cursor.insertText(text + closer)
        cursor.movePosition(QTextCursor.MoveOperation.PreviousCharacter)
        self.setTextCursor(cursor)
        return True

    def _handle_skip_close(self, text: str, cursor: QTextCursor) -> bool:
        """Closing char: skip-over instead of inserting duplicate."""
        if text in _CLOSE_CHARS and self._char_after_cursor(cursor) == text:
            cursor.movePosition(QTextCursor.MoveOperation.NextCharacter)
            self.setTextCursor(cursor)
            return True
        return False

    # ── Advanced features ─────────────────────────────────────────────

    def _auto_format_json(self) -> None:
        """Auto-format JSON with proper indentation. Ctrl+Shift+F"""
        try:
            text = self.toPlainText().strip()
            if not text:
                return
            
            # Parse and reformat
            parsed = _json.loads(text)
            formatted = _json.dumps(parsed, indent=self._INDENT_SIZE, ensure_ascii=False)
            
            cursor = self.textCursor()
            cursor.select(QTextCursor.SelectionType.Document)
            cursor.removeSelectedText()
            cursor.insertText(formatted)
            
            # Move cursor to beginning
            cursor.movePosition(QTextCursor.MoveOperation.Start)
            self.setTextCursor(cursor)
        except Exception:
            # Invalid JSON — silently skip formatting
            pass

    def _toggle_comment(self) -> None:
        """Toggle comment for current line(s). Ctrl+/"""
        cursor = self.textCursor()
        
        if cursor.hasSelection():
            # Comment/uncomment selected lines
            self._toggle_comment_lines(cursor)
        else:
            # Toggle current line
            block = cursor.block()
            self._toggle_comment_line(block, cursor)

    def _toggle_comment_lines(self, cursor: QTextCursor) -> None:
        """Toggle comment for multiple lines."""
        start_block = self.document().findBlock(cursor.selectionStart())
        end_block = self.document().findBlock(cursor.selectionEnd() - 1)
        
        # Check if any line is uncommented
        block = start_block
        has_uncommented = False
        while block.isValid() and block.blockNumber() <= end_block.blockNumber():
            text = block.text().strip()
            if text and not text.startswith("//"):
                has_uncommented = True
                break
            block = block.next()
        
        # Toggle all lines
        block = start_block
        while block.isValid() and block.blockNumber() <= end_block.blockNumber():
            if has_uncommented:
                self._comment_line(block)
            else:
                self._uncomment_line(block)
            block = block.next()

    def _toggle_comment_line(self, block, cursor: QTextCursor) -> None:
        """Toggle comment for a single line."""
        text = block.text().strip()
        if text.startswith("//"):
            self._uncomment_line(block)
        elif text:
            self._comment_line(block)

    def _comment_line(self, block) -> None:
        """Add // comment to a line."""
        cursor = QTextCursor(block)
        # Find first non-space character
        text = block.text()
        indent = len(text) - len(text.lstrip())
        cursor.movePosition(QTextCursor.MoveOperation.StartOfBlock)
        cursor.movePosition(QTextCursor.MoveOperation.Right, QTextCursor.MoveMode.MoveAnchor, indent)
        cursor.insertText("// ")

    def _uncomment_line(self, block) -> None:
        """Remove // comment from a line."""
        text = block.text()
        indent = len(text) - len(text.lstrip())
        stripped = text[indent:]
        
        if stripped.startswith("// "):
            cursor = QTextCursor(block)
            cursor.movePosition(QTextCursor.MoveOperation.StartOfBlock)
            cursor.movePosition(QTextCursor.MoveOperation.Right, QTextCursor.MoveMode.MoveAnchor, indent)
            for _ in range(3):  # Remove "// "
                cursor.deleteChar()
        elif stripped.startswith("//"):
            cursor = QTextCursor(block)
            cursor.movePosition(QTextCursor.MoveOperation.StartOfBlock)
            cursor.movePosition(QTextCursor.MoveOperation.Right, QTextCursor.MoveMode.MoveAnchor, indent)
            for _ in range(2):  # Remove "//"
                cursor.deleteChar()

    def _increase_indent(self) -> None:
        """Increase indent for selected lines."""
        cursor = self.textCursor()
        start_block = self.document().findBlock(cursor.selectionStart())
        end_block = self.document().findBlock(cursor.selectionEnd() - 1)
        
        block = start_block
        while block.isValid() and block.blockNumber() <= end_block.blockNumber():
            text = block.text()
            if text.strip():  # Only indent non-empty lines
                cursor = QTextCursor(block)
                cursor.movePosition(QTextCursor.MoveOperation.StartOfBlock)
                cursor.insertText(" " * self._INDENT_SIZE)
            block = block.next()

    def decrease_indent(self) -> None:
        """Decrease indent for selected lines (Shift+Tab)."""
        cursor = self.textCursor()
        if cursor.hasSelection():
            start_block = self.document().findBlock(cursor.selectionStart())
            end_block = self.document().findBlock(cursor.selectionEnd() - 1)
            
            block = start_block
            while block.isValid() and block.blockNumber() <= end_block.blockNumber():
                text = block.text()
                if text.startswith(" " * self._INDENT_SIZE):
                    cursor = QTextCursor(block)
                    cursor.movePosition(QTextCursor.MoveOperation.StartOfBlock)
                    for _ in range(self._INDENT_SIZE):
                        cursor.deleteChar()
                block = block.next()

    # ── Helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _char_before_cursor(cursor: QTextCursor) -> str:
        clone = QTextCursor(cursor)
        clone.movePosition(QTextCursor.MoveOperation.PreviousCharacter, QTextCursor.MoveMode.KeepAnchor)
        return clone.selectedText()

    @staticmethod
    def _char_after_cursor(cursor: QTextCursor) -> str:
        clone = QTextCursor(cursor)
        clone.movePosition(QTextCursor.MoveOperation.NextCharacter, QTextCursor.MoveMode.KeepAnchor)
        return clone.selectedText()

