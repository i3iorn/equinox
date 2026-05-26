from __future__ import annotations

from typing import List, Optional

from PyQt6.QtGui import QSyntaxHighlighter, QTextDocument

from ..lexer import JsonLexer, State, Token
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
    """JSON/JSONC syntax highlighter using streaming lexer."""

    def __init__(self, document: QTextDocument) -> None:
        super().__init__(document)
        self.lexer = JsonLexer()
        self.formats = build_token_formats()

    def highlightBlock(self, text: Optional[str]) -> None:  # noqa: N802
        text = text or ""
        prev_state = self.previousBlockState()

        try:
            initial_state = State(prev_state) if prev_state != -1 else State.NORMAL
        except ValueError:
            initial_state = State.NORMAL

        tokens, final_state = self._lex_line(text, initial_state)
        self._apply_token_formats(tokens)
        self._apply_variable_highlighting(text)
        self.setCurrentBlockState(final_state.value)

    def _lex_line(self, text: str, state: State) -> tuple[list[Token], State]:
        """Lex a single line into tokens and final state."""
        gen = self.lexer.tokenize_line(text, state)
        tokens: list[Token] = []
        final_state = state
        try:
            while True:
                tokens.append(next(gen))
        except StopIteration as exc:
            if exc.value is not None:
                final_state = exc.value
        return tokens, final_state

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
        for match in _VARIABLE_PATTERN.finditer(text):
            self.setFormat(match.start(), match.end() - match.start(), _VARIABLE_FMT)
