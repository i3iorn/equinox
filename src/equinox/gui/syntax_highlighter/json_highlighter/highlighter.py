from __future__ import annotations

from typing import List, Optional

from PyQt6.QtGui import QSyntaxHighlighter, QTextDocument

from equinox.core.json_tools.lexer import JsonLexer
from equinox.core.json_tools.tokens import Token

from .formats import _VARIABLE_FMT, _VARIABLE_PATTERN, build_token_formats


def _is_key(tokens: List[Token], index: int) -> bool:
    """Return True if token at index should be treated as a JSON key."""
    tok = tokens[index]
    if tok.type not in ("STRING", "TIMESTAMP"):
        return False
    if index + 1 >= len(tokens):
        return False
    return tokens[index + 1].type == ":"


class JsonHighlighter(QSyntaxHighlighter):
    """JSON/JSONC syntax highlighter using the new lexer."""

    def __init__(self, document: QTextDocument) -> None:
        super().__init__(document)
        self.lexer = JsonLexer(allow_comments=True, detect_timestamps=True)
        self.formats = build_token_formats()

    def highlightBlock(self, text: Optional[str]) -> None:  # noqa: N802
        text = text or ""

        # Retrieve previous block state
        prev_state = self.previousBlockState()
        state = prev_state if prev_state != -1 else "NORMAL"

        # Lex this line
        tokens, next_state = self.lexer.tokenize_line(text, str(state))

        # Apply syntax highlighting
        self._apply_token_formats(tokens)
        self._apply_variable_highlighting(text)

        # Store next state for the next line
        self.setCurrentBlockState({"NORMAL": 0, "STRING": 1, "COMMENT_BLOCK": 2}[next_state])

    def _apply_token_formats(self, tokens: List[Token]) -> None:
        """Apply syntax formats for all tokens."""
        for index, tok in enumerate(tokens):
            token_type = "KEY" if _is_key(tokens, index) else tok.type
            fmt = self.formats.get(token_type, self.formats["ERROR"])
            self.setFormat(tok.start, tok.end - tok.start, fmt)

    def _apply_variable_highlighting(self, text: str) -> None:
        """Apply {{variable}} placeholder highlighting with highest precedence."""
        if "{{" not in text:
            return
        for match in _VARIABLE_PATTERN.finditer(text):
            self.setFormat(match.start(), match.end() - match.start(), _VARIABLE_FMT)
