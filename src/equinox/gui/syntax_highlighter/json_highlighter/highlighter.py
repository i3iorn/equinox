from __future__ import annotations

from equinox.core.json_tools.lexer import JsonLexer
from equinox.core.json_tools.lexer import JsonLexerConfig
from equinox.core.json_tools.tokens import Token
from PyQt6.QtGui import QSyntaxHighlighter
from PyQt6.QtGui import QTextDocument

from ..base import _variable_fmt
from ..base import _VARIABLE_PATTERN
from ..base import register_highlighter
from .formats import build_token_formats


def _is_key(tokens: list[Token], index: int) -> bool:
    """Return True if token at index should be treated as a JSON key."""
    tok = tokens[index]
    if tok.type not in ("STRING", "TIMESTAMP"):
        return False
    if index + 1 >= len(tokens):
        return False
    return bool(tokens[index + 1].type == ":")


class JsonHighlighter(QSyntaxHighlighter):
    """JSON/JSONC syntax highlighter using the new lexer."""

    def __init__(self, document: QTextDocument) -> None:
        super().__init__(document)
        self.lexer = JsonLexer(JsonLexerConfig(allow_comments=True, detect_timestamps=True))
        self.formats = build_token_formats()
        register_highlighter(self)

    def refresh_theme(self) -> None:
        """Rebuild token formats for the current theme and repaint.

        ``build_token_formats()`` reads ``Colors.*`` at call time, but
        ``self.formats`` is otherwise only built once in ``__init__`` — so
        without this, an already-open JSON body keeps stale colors after a
        theme switch. Called by ``base.notify_theme_changed()``.
        """
        self.formats = build_token_formats()
        self.rehighlight()

    def highlightBlock(self, text: str | None) -> None:
        text = text or ""

        # Retrieve previous block state
        prev_state = self.previousBlockState()
        state = prev_state if prev_state != -1 else "NORMAL"

        # Lex this line
        tokens, next_state = self.lexer.tokenize_line(text, state)

        # Apply syntax highlighting
        self._apply_token_formats(tokens)
        self._apply_variable_highlighting(text)

        # Store next state for the next line
        self.setCurrentBlockState({"NORMAL": 0, "STRING": 1, "COMMENT_BLOCK": 2}[next_state.value])

    def _apply_token_formats(self, tokens: list[Token]) -> None:
        """Apply syntax formats for all tokens."""
        for index, tok in enumerate(tokens):
            token_type = "KEY" if _is_key(tokens, index) else tok.type
            fmt = self.formats.get(token_type, self.formats["ERROR"])
            self.setFormat(tok.start, tok.end - tok.start, fmt)

    def _apply_variable_highlighting(self, text: str) -> None:
        """Apply {{variable}} placeholder highlighting with highest precedence."""
        if "{{" not in text:
            return
        fmt = _variable_fmt()
        for match in _VARIABLE_PATTERN.finditer(text):
            self.setFormat(match.start(), match.end() - match.start(), fmt)
