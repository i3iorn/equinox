"""JSON body editor with bracket-matching and auto-indent."""

from PyQt6.QtWidgets import QTextEdit
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QTextCursor

_OPEN_CLOSE = {"{": "}", "[": "]", '"': '"'}
_CLOSE_CHARS = set(_OPEN_CLOSE.values())


class JsonBodyEditor(QTextEdit):
    """QTextEdit with JSON-friendly editing helpers:

    - Auto-close ``{``, ``[``, and ``"`` (inserts matching closer, positions
      cursor between them).
    - Smart ``"``-handling: if the cursor is already sitting before a ``"``,
      just move past it instead of inserting a second one.
    - Tab inserts 4 spaces (never a hard tab character).
    - Enter replicates the current line's leading whitespace and, when the
      previous non-empty line ends with ``{`` or ``[``, indents one extra
      level (4 spaces).
    - Backspace removes a matching auto-close pair when the cursor is between
      them (e.g. ``{|}`` → removes ``{``).
    """

    _INDENT_SIZE = 4

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        key = event.key()
        modifiers = event.modifiers()
        cursor = self.textCursor()

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

    def _handle_tab(self, key: int, modifiers, cursor: QTextCursor) -> bool:
        """Tab → 4 spaces."""
        if key == Qt.Key.Key_Tab and not modifiers:
            cursor.insertText(" " * self._INDENT_SIZE)
            return True
        return False

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
        """Backspace → remove auto-close pair."""
        if key != Qt.Key.Key_Backspace or modifiers or cursor.hasSelection():
            return False
        char_before = self._char_before_cursor(cursor)
        char_after = self._char_after_cursor(cursor)
        if char_before and char_after and _OPEN_CLOSE.get(char_before) == char_after:
            cursor.deletePreviousChar()
            cursor.deleteChar()
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

