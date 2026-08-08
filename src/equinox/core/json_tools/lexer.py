from __future__ import annotations

import logging
from collections.abc import Generator
from dataclasses import dataclass
from enum import Enum

from .patterns import CONTROL_CHAR_THRESHOLD
from .patterns import JSON5_LITERALS
from .patterns import JSON_ESCAPE_CHARS
from .patterns import NUMBER_RE
from .patterns import TIMESTAMP_RE
from .patterns import UNICODE_HEX_RE
from .tokens import Token

logger = logging.getLogger(__name__)


class LexerError(Exception):
    """Base exception for lexer-related errors."""


class InvalidLexerStateError(LexerError):
    """Raised when an invalid lexer state is provided."""


class LexerState(str, Enum):
    """Lexing states for JSON-like input."""

    NORMAL = "NORMAL"
    STRING = "STRING"
    COMMENT_BLOCK = "COMMENT_BLOCK"

    @classmethod
    def from_string(cls, state_str: str) -> LexerState:
        """Convert a string to a LexerState enum member.

        Args:
            state_str: The string representation of the state.

        Returns:
            The corresponding LexerState member.

        Raises:
            InvalidLexerStateError: If the string does not correspond to a valid state.
        """
        try:
            return cls[state_str.upper()]
        except KeyError as exc:
            raise InvalidLexerStateError(f"Unrecognized lexer state: {state_str!r}") from exc

    @classmethod
    def from_int(cls, state_int: int) -> LexerState:
        """Convert an integer to a LexerState enum member.

        This is used for compatibility with QSyntaxHighlighter block states.

        Args:
            state_int: The integer representation of the state (0, 1, 2).
        """
        try:
            if state_int == 0:
                return cls.NORMAL
            elif state_int == 1:
                return cls.STRING
            elif state_int == 2:
                return cls.COMMENT_BLOCK
            else:
                raise InvalidLexerStateError(f"Unrecognized lexer state: {state_int}")
        except ValueError as exc:
            raise InvalidLexerStateError(
                f"Unrecognized lexer state integer: {state_int!r}",
            ) from exc


@dataclass(frozen=True)
class JsonLexerConfig:
    """Configuration for JsonLexer behavior.

    Attributes:
        allow_comments: Whether to recognize line and block comments.
        detect_timestamps: Whether to classify matching strings as TIMESTAMP tokens.
    """

    allow_comments: bool = True
    detect_timestamps: bool = True


class JsonLexer:
    """Lexer for JSON/JSON5-like text.

    The lexer produces Token instances from an input string. It is designed to be
    deterministic, side-effect free, and suitable for streaming or line-by-line use.
    """

    def __init__(
        self,
        config: JsonLexerConfig | None = None,
        *,
        allow_comments: bool | None = None,
        detect_timestamps: bool | None = None,
    ) -> None:
        """Initialize the lexer with the given configuration.

        Args:
            config: Optional JsonLexerConfig. If omitted, secure defaults are used.
        """
        base_config = config or JsonLexerConfig()
        resolved_allow_comments = (
            base_config.allow_comments if allow_comments is None else allow_comments
        )
        resolved_detect_timestamps = (
            base_config.detect_timestamps if detect_timestamps is None else detect_timestamps
        )
        self._config = JsonLexerConfig(
            allow_comments=resolved_allow_comments,
            detect_timestamps=resolved_detect_timestamps,
        )

    # ------------------------------------------------------------
    # PUBLIC API #1: Full-document streaming
    # ------------------------------------------------------------
    def tokenize(self, text: str) -> Generator[Token]:
        """Tokenize an entire document as a single stream.

        Args:
            text: The input text to tokenize.

        Yields:
            Token objects representing the lexed input.

        Raises:
            TypeError: If text is not a string.
        """
        self._validate_text_input(text)

        state = LexerState.NORMAL
        index = 0
        length = len(text)
        string_start = 0

        while index < length:
            index, state, token, string_start = self._step(text, index, state, string_start)
            if token is not None:
                yield token
        eof_token = self._finalize_document_state(length, state, string_start)
        if eof_token is not None:
            yield eof_token

    # ------------------------------------------------------------
    # PUBLIC API #2: Line-by-line tokenization
    # ------------------------------------------------------------
    def tokenize_line_by_line(
        self,
        text: str,
    ) -> Generator[tuple[list[Token], LexerState]]:
        """Tokenize a multi-line string, yielding tokens and state per line.

        This is suitable for syntax highlighters that process text incrementally.

        Args:
            text: The multi-line input text.

        Yields:
            Tuples of (tokens_for_line, resulting_state).

        Raises:
            TypeError: If text is not a string.
        """
        self._validate_text_input(text)

        state: LexerState = LexerState.NORMAL
        for line in text.splitlines(keepends=False):
            tokens, state = self.tokenize_line(line, state)
            yield tokens, state

    # ------------------------------------------------------------
    # PUBLIC API #3: Single-line lexing (used by QSyntaxHighlighter)
    # ------------------------------------------------------------
    def tokenize_line(
        self,
        text: str,
        state: LexerState | str | int,
    ) -> tuple[list[Token], LexerState]:
        """Tokenize a single line of text given an initial lexer state.

        Args:
            text: The line of text to tokenize.
            state: The starting lexer state, as a LexerState or its name.

        Returns:
            A tuple of (tokens, resulting_state).

        Raises:
            TypeError: If text is not a string.
            InvalidLexerStateError: If state is not a recognized lexer state.
        """
        self._validate_text_input(text)
        normalized_state = self._normalize_state(state)

        tokens: list[Token] = []
        index = 0
        length = len(text)
        string_start = 0

        while index < length:
            index, normalized_state, token, string_start = self._step(
                text,
                index,
                normalized_state,
                string_start,
            )
            if token is not None:
                tokens.append(token)

        return tokens, normalized_state

    # ------------------------------------------------------------
    # INTERNAL: Input validation and state normalization
    # ------------------------------------------------------------
    @staticmethod
    def _validate_text_input(text: str) -> None:
        """Validate that the provided text input is a string.

        Args:
            text: The value to validate.

        Raises:
            TypeError: If text is not a string.
        """
        if not isinstance(text, str):
            raise TypeError("text must be a string")

    @staticmethod
    def _normalize_state(state: LexerState | str | int) -> LexerState:
        """Normalize a state value into a LexerState enum.

        Args:
            state: A LexerState instance or its string name.

        Returns:
            A LexerState instance.

        Raises:
            InvalidLexerStateError: If the state is not recognized.
        """
        if isinstance(state, LexerState):
            return state

        if isinstance(state, str):
            return LexerState.from_string(state)

        if isinstance(state, int):
            return LexerState.from_int(state)

        raise InvalidLexerStateError(f"Invalid lexer state type: {type(state)!r}")

    @staticmethod
    def _finalize_document_state(
        length: int,
        state: LexerState,
        string_start: int,
    ) -> Token | None:
        """Return an EOF error token when the final lexer state is incomplete."""
        if state == LexerState.STRING:
            return Token("ERROR_STRING", string_start, length, "")
        if state == LexerState.COMMENT_BLOCK:
            return Token("ERROR_COMMENT", string_start, length, "")
        return None

    # ------------------------------------------------------------
    # INTERNAL: One lexing step (delegates by mode)
    # ------------------------------------------------------------
    def _step(
        self,
        text: str,
        index: int,
        state: LexerState,
        string_start: int,
    ) -> tuple[int, LexerState, Token | None, int]:
        """Advance the lexer by one logical step.

        Args:
            text: The full input text.
            index: Current index in the text.
            state: Current lexer state.
            string_start: Index where the current string or comment started.

        Returns:
            A tuple of (next_index, next_state, token_or_none, next_string_start).
        """
        if state == LexerState.STRING:
            return self._step_string_mode(text, index, string_start)

        if state == LexerState.COMMENT_BLOCK:
            return self._step_comment_block_mode(text, index, string_start)

        return self._step_normal_mode(text, index, string_start)

    # ------------------------------------------------------------
    # INTERNAL: STRING MODE
    # ------------------------------------------------------------
    def _step_string_mode(
        self,
        text: str,
        index: int,
        string_start: int,
    ) -> tuple[int, LexerState, Token | None, int]:
        """Handle lexing while inside a string literal."""
        ch = text[index]
        length = len(text)

        if ch == '"':
            end = index + 1
            value = text[string_start + 1 : index]
            token_type = "STRING"
            if self._config.detect_timestamps and TIMESTAMP_RE.fullmatch(value):
                token_type = "TIMESTAMP"
            token = Token(token_type, string_start, end, value)
            return end, LexerState.NORMAL, token, string_start

        if ch == "\\":
            if index + 1 >= length:
                token = Token("ERROR_STRING", index, index + 1, ch)
                logger.debug("Unterminated escape sequence at index %d", index)
                return index + 1, LexerState.STRING, token, string_start

            esc = text[index + 1]
            if esc == "u":
                if not UNICODE_HEX_RE.match(text, index + 2, index + 6):
                    value = text[index : index + 2]
                    token = Token("ERROR_STRING", index, index + 2, value)
                    logger.debug("Invalid unicode escape at index %d", index)
                    return index + 2, LexerState.STRING, token, string_start
                return index + 6, LexerState.STRING, None, string_start

            if esc not in JSON_ESCAPE_CHARS:
                value = text[index : index + 2]
                token = Token("ERROR_STRING", index, index + 2, value)
                logger.debug("Invalid escape sequence at index %d", index)
                return index + 2, LexerState.STRING, token, string_start

            return index + 2, LexerState.STRING, None, string_start

        if ord(ch) < CONTROL_CHAR_THRESHOLD:
            token = Token("ERROR_STRING", index, index + 1, ch)
            logger.debug("Control character in string at index %d", index)
            return index + 1, LexerState.STRING, token, string_start

        return index + 1, LexerState.STRING, None, string_start

    # ------------------------------------------------------------
    # INTERNAL: COMMENT BLOCK MODE
    # ------------------------------------------------------------
    def _step_comment_block_mode(
        self,
        text: str,
        index: int,
        string_start: int,
    ) -> tuple[int, LexerState, Token | None, int]:
        """Handle lexing while inside a block comment."""
        if text.startswith("*/", index):
            end = index + 2
            value = text[string_start:end]
            token = Token("COMMENT", string_start, end, value)
            return end, LexerState.NORMAL, token, string_start

        return index + 1, LexerState.COMMENT_BLOCK, None, string_start

    # ------------------------------------------------------------
    # INTERNAL: NORMAL MODE
    # ------------------------------------------------------------
    def _step_normal_mode(
        self,
        text: str,
        index: int,
        string_start: int,
    ) -> tuple[int, LexerState, Token | None, int]:
        """Handle lexing while in the normal (top-level) mode."""
        ch = text[index]
        length = len(text)

        if ch.isspace():
            return index + 1, LexerState.NORMAL, None, string_start

        if self._config.allow_comments and text.startswith("//", index):
            value = text[index:length]
            token = Token("COMMENT", index, length, value)
            return length, LexerState.NORMAL, token, string_start

        if self._config.allow_comments and text.startswith("/*", index):
            return index + 2, LexerState.COMMENT_BLOCK, None, index

        if ch in "{}[]:,":
            token = Token(ch, index, index + 1, ch)
            return index + 1, LexerState.NORMAL, token, string_start

        if ch == '"':
            return index + 1, LexerState.STRING, None, index

        number_match = NUMBER_RE.match(text, index)
        if number_match:
            end = number_match.end()
            value = number_match.group(0)
            token = Token("NUMBER", index, end, value)
            return end, LexerState.NORMAL, token, string_start

        for literal, token_type in JSON5_LITERALS.items():
            if text.startswith(literal, index):
                end = index + len(literal)
                token = Token(token_type, index, end, literal)
                return end, LexerState.NORMAL, token, string_start

        token = Token("ERROR", index, index + 1, ch)
        logger.debug("Unexpected character at index %d", index)
        return index + 1, LexerState.NORMAL, token, string_start
