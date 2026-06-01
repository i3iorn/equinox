from __future__ import annotations

import logging
from collections.abc import Generator

from .comment_handler import consume_block_comment
from .comment_handler import try_block_comment_start
from .comment_handler import try_line_comment
from .escape_validator import validate_escape
from .patterns import CONTROL_CHAR_THRESHOLD
from .patterns import JSON5_LITERALS
from .patterns import NUMBER_RE
from .patterns import Token
from .states import State
from .timestamp_detector import detect_string_token_type

logger = logging.getLogger(__name__)


class JsonLexer:
    """Streaming JSON/JSONC lexer that handles multi-line state."""

    def __init__(self, *, enable_comments: bool = True, enable_timestamps: bool = True) -> None:
        self.enable_comments = enable_comments
        self.enable_timestamps = enable_timestamps

    def tokenize_line(self, text: str, state: State) -> Generator[Token, None, State]:
        """Tokenize one line of JSON/JSONC text with strict SRP dispatch."""
        index = 0
        length = len(text)
        string_opened = False
        string_start = 0

        while index < length:
            if state == State.STRING:
                index, tok, state, string_opened = self._lex_string(
                    text, index, string_start, string_opened,
                )
                yield tok
                if state == State.STRING and index >= length:
                    return State.STRING
                continue

            if state == State.COMMENT_BLOCK:
                index, tok, closed = consume_block_comment(text, index)
                yield tok
                if not closed:
                    return State.COMMENT_BLOCK
                state = State.NORMAL
                continue

            index, state, normal_tok, string_opened, string_start = self._lex_normal(
                text, index, state, string_opened, string_start,
            )
            if normal_tok is not None:
                yield normal_tok

        return state

    def _lex_normal(
        self, text: str, index: int, state: State, opened: bool, start: int,
    ) -> tuple[int, State, Token | None, bool, int]:
        ch = text[index]

        if ch.isspace():
            return index + 1, state, None, opened, start

        if self.enable_comments:
            line_comment = try_line_comment(text, index)
            if line_comment:
                return len(text), state, line_comment, opened, start

            if try_block_comment_start(text, index):
                return index + 2, State.COMMENT_BLOCK, None, opened, start

        if ch in "{}[]:,":
            return index + 1, state, Token(ch, index, index + 1, ch), opened, start

        if ch == '"':
            return index + 1, State.STRING, None, True, index

        if ch == "-" or ch.isdigit():
            m = NUMBER_RE.match(text, index)
            if m:
                end = m.end()
                return end, state, Token("NUMBER", index, end, m.group(0)), opened, start
            return index + 1, state, Token("ERROR_NUMBER", index, index + 1, ch), opened, start

        lit = self._match_literal(text, index)
        if lit:
            ttype, end = lit
            return end, state, Token(ttype, index, end, text[index:end]), opened, start

        return index + 1, state, Token("ERROR", index, index + 1, ch), opened, start

    def _lex_string(
        self, text: str, index: int, start: int, opened: bool,
    ) -> tuple[int, Token, State, bool]:
        length = len(text)
        while index < length:
            c = text[index]

            if c == '"':
                end = index + 1
                value = text[start + 1 : index]
                ttype = detect_string_token_type(value, self.enable_timestamps)
                return end, Token(ttype, start, end, value), State.NORMAL, False

            if c == "\\":
                new_i, err = validate_escape(text, index)
                if err:
                    return new_i, err, State.STRING, opened
                index = new_i
                continue

            if ord(c) < CONTROL_CHAR_THRESHOLD:
                return index + 1, Token("ERROR_STRING", index, index + 1, c), State.STRING, opened

            index += 1

        value = text[start + 1 : length]
        return length, Token("STRING", start, length, value), State.STRING, opened

    def _match_literal(self, text: str, index: int) -> tuple[str, int] | None:
        """Return (token_type, end_index) if a JSON5 literal matches."""
        for literal, token_type in JSON5_LITERALS.items():
            if text.startswith(literal, index):
                return token_type, index + len(literal)
        return None

    def _handle_string_state(
        self,
        text: str,
        index: int,
        state: State,
        string_start_index: int,
        string_opened_here: bool,
    ) -> tuple[int, Token, State, bool]:
        """Handle characters while in STRING state."""
        length = len(text)
        start = string_start_index if not string_opened_here else string_start_index

        while index < length:
            c = text[index]

            if c == '"':
                end = index + 1
                content_start = start + 1
                value = text[content_start:index]
                token_type = detect_string_token_type(value, self.enable_timestamps)
                token = Token(token_type, start, end, value)
                return end, token, State.NORMAL, False

            if c == "\\":
                new_index, error_token = validate_escape(text, index)
                if error_token is not None:
                    return new_index, error_token, State.STRING, string_opened_here
                index = new_index
                continue

            if ord(c) < CONTROL_CHAR_THRESHOLD:
                token = Token("ERROR_STRING", index, index + 1, c)
                return index + 1, token, State.STRING, string_opened_here

            index += 1

        content_start = start + 1
        value = text[content_start:length]
        token = Token("STRING", start, length, value)
        return length, token, State.STRING, string_opened_here
