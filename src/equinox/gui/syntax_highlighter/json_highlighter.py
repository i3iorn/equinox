"""Streaming JSON/JSONC lexer and PyQt6 syntax highlighter."""

import re
from enum import Enum
from typing import Generator, NamedTuple

from PyQt6.QtGui import QSyntaxHighlighter

from equinox.gui.syntax_highlighter.base import _make_format, _VARIABLE_FMT, _VARIABLE_PATTERN
from equinox.gui.theme import Colors

__all__ = ["JsonHighlighter", "JsonLexer", "State"]


# ---------------------------------------------------------------------------
# Module-level compiled patterns (immutable, compiled once)
# ---------------------------------------------------------------------------

_NUMBER_RE = re.compile(r"-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?")

# ISO-8601 timestamp: YYYY-MM-DD THH:MM:SS[.fractional][timezone]
_TIMESTAMP_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?"
)

# Unicode escape sequence validator (\uXXXX)
_UNICODE_HEX_RE = re.compile(r"[0-9a-fA-F]{4}")

# JSON5 literal keywords (lowercase name → uppercase token type)
_JSON5_LITERALS: dict[str, str] = {
    "true": "TRUE",
    "false": "FALSE",
    "null": "NULL",
}

# Valid escape sequences in JSON strings
_JSON_ESCAPE_CHARS: frozenset[str] = frozenset(r'"\\/bfnrt')

# Control character threshold — characters below this are errors in JSON strings
_CONTROL_CHAR_THRESHOLD: int = 0x20


# ---------------------------------------------------------------------------
# Lexer state machine
# ---------------------------------------------------------------------------

class State(Enum):
    """Lexer state for multi-line context tracking."""

    NORMAL = 0
    STRING = 1
    COMMENT_BLOCK = 2


class Token(NamedTuple):
    """Immutable lexer token.

    NamedTuple is faster than dataclass and is naturally immutable — tokens
    are never mutated after creation.
    """

    type: str
    start: int
    end: int
    value: str


# ---------------------------------------------------------------------------
# Lexer
# ---------------------------------------------------------------------------

class JsonLexer:
    """Streaming JSON/JSONC lexer that handles multi-line state.

    Tokenizes one line at a time, tracking state across lines for strings and
    block comments. Supports optional JSON5 extensions (comments, timestamps).
    """

    def __init__(
        self, *, enable_comments: bool = True, enable_timestamps: bool = True
    ) -> None:
        """Initialize lexer with optional JSON5 extensions.

        Parameters
        ----------
        enable_comments
            Allow // and /* */ comments (JSON5 extension).
        enable_timestamps
            Recognize ISO-8601 timestamps in string values.
        """
        self.enable_comments = enable_comments
        self.enable_timestamps = enable_timestamps

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def tokenize_line(
        self, text: str, state: State
    ) -> Generator[Token, None, State]:
        """Tokenize one line, returning tokens and the final state.

        The generator's return value (accessed via StopIteration.value) is the
        final state after processing the line — use it for `setCurrentBlockState`.

        Parameters
        ----------
        text
            The text to tokenize.
        state
            The lexer state at the start of the line (from previous line).

        Yields
        ------
        Token
            Each token found in the line.

        Returns
        -------
        State
            The final state after processing.
        """
        i = 0
        n = len(text)
        string_opened_here = False
        string_start_index = 0

        while i < n:
            ch = text[i]

            # =====================================================================
            # STRING STATE — inside a quoted string
            # =====================================================================

            if state == State.STRING:
                start = string_start_index if string_opened_here else i

                while i < n:
                    c = text[i]

                    if c == '"':
                        # String close
                        i += 1
                        state = State.NORMAL
                        content_start = (start + 1) if string_opened_here else start
                        value = text[content_start : i - 1]
                        # Emit the appropriate token (TIMESTAMP or STRING)
                        if self.enable_timestamps and _TIMESTAMP_RE.fullmatch(value):
                            yield Token("TIMESTAMP", start, i, value)
                        else:
                            yield Token("STRING", start, i, value)
                        string_opened_here = False
                        break

                    elif c == "\\":
                        # Escape sequence
                        if i + 1 >= n:
                            yield Token("ERROR_STRING", i, i + 1, c)
                            i += 1
                            continue

                        esc = text[i + 1]

                        if esc == "u":
                            # Unicode escape \uXXXX
                            if _UNICODE_HEX_RE.match(text, i + 2, i + 6):
                                i += 6
                            else:
                                yield Token("ERROR_STRING", i, i + 2, text[i : i + 2])
                                i += 2
                        elif esc in _JSON_ESCAPE_CHARS:
                            i += 2
                        else:
                            yield Token("ERROR_STRING", i, i + 2, text[i : i + 2])
                            i += 2

                    else:
                        # Regular character or control char
                        if ord(c) < _CONTROL_CHAR_THRESHOLD:
                            yield Token("ERROR_STRING", i, i + 1, c)
                        i += 1

                else:
                    # Unterminated string — carries to next line
                    content_start = (start + 1) if string_opened_here else start
                    yield Token("STRING", start, n, text[content_start:])
                    return State.STRING

                continue

            # =====================================================================
            # BLOCK COMMENT STATE — inside /* ... */
            # =====================================================================

            if state == State.COMMENT_BLOCK:
                start = i
                while i < n:
                    if text[i : i + 2] == "*/":
                        i += 2
                        state = State.NORMAL
                        yield Token("COMMENT", start, i, text[start:i])
                        break
                    i += 1
                else:
                    yield Token("COMMENT", start, n, text[start:])
                    return State.COMMENT_BLOCK
                continue

            # =====================================================================
            # NORMAL STATE
            # =====================================================================

            if ch.isspace():
                i += 1
                continue

            # JSON5 comments
            if self.enable_comments:
                if text.startswith("//", i):
                    yield Token("COMMENT", i, n, text[i:])
                    return State.NORMAL
                if text.startswith("/*", i):
                    state = State.COMMENT_BLOCK
                    i += 2
                    continue

            # Structure characters: { } [ ] : ,
            if ch in "{}[]:,":
                yield Token(ch, i, i + 1, ch)
                i += 1
                continue

            # String start
            if ch == '"':
                string_opened_here = True
                string_start_index = i
                state = State.STRING
                i += 1
                continue

            # Number
            if ch == "-" or ch.isdigit():
                m = _NUMBER_RE.match(text, i)
                if m:
                    yield Token("NUMBER", i, m.end(), m.group(0))
                    i = m.end()
                else:
                    yield Token("ERROR_NUMBER", i, i + 1, ch)
                    i += 1
                continue

            # JSON5 literals: true, false, null
            matched = False
            for literal, token_type in _JSON5_LITERALS.items():
                if text.startswith(literal, i):
                    yield Token(token_type, i, i + len(literal), literal)
                    i += len(literal)
                    matched = True
                    break
            if matched:
                continue

            # Unrecognized character
            yield Token("ERROR", i, i + 1, ch)
            i += 1

        return state



# ---------------------------------------------------------------------------
# Highlighter
# ---------------------------------------------------------------------------

class JsonHighlighter(QSyntaxHighlighter):
    """JSON/JSONC syntax highlighter using streaming lexer.

    Handles multi-line strings and block comments. Supports JSON5 extensions
    (comments, timestamps). Highlights variables (``{{var}}``) last so they
    override language-specific formats.
    """

    # Format definitions (frozen at class load time)
    _FORMAT_MAP: dict[str, str] = {
        "STRING": Colors.GREEN,
        "TIMESTAMP": Colors.TEAL,
        "NUMBER": Colors.PURPLE,
        "TRUE": Colors.AMBER,
        "FALSE": Colors.AMBER,
        "NULL": Colors.AMBER,
        "COMMENT": Colors.GRAY,
        "{": Colors.FG_MUTED,
        "}": Colors.FG_MUTED,
        "[": Colors.FG_MUTED,
        "]": Colors.FG_MUTED,
        ":": Colors.FG_MUTED,
        ",": Colors.FG_MUTED,
        "KEY": Colors.BLUE,
        "ERROR": Colors.RED,
        "ERROR_STRING": Colors.RED,
        "ERROR_NUMBER": Colors.RED,
    }

    # Format style modifiers (additional properties like bold, italic)
    _FORMAT_STYLES: dict[str, dict[str, bool]] = {
        "TIMESTAMP": {"italic": True},
        "TRUE": {"bold": True},
        "FALSE": {"bold": True},
        "NULL": {"bold": True},
        "COMMENT": {"italic": True},
        "{": {"bold": True},
        "}": {"bold": True},
        "[": {"bold": True},
        "]": {"bold": True},
        "KEY": {"bold": True},
        "ERROR": {"bold": True},
        "ERROR_STRING": {"underline": True},
        "ERROR_NUMBER": {"bold": True},
    }

    def __init__(self, document) -> None:
        """Initialize the highlighter.

        Parameters
        ----------
        document
            The QTextDocument to highlight.
        """
        super().__init__(document)
        self.lexer = JsonLexer()
        # Pre-build format map at init time (one-time cost)
        self.formats = self._build_formats()

    @classmethod
    def _build_formats(cls) -> dict[str, object]:
        """Build QTextCharFormat map from color and style specs."""
        formats: dict[str, object] = {}
        for token_type, color in cls._FORMAT_MAP.items():
            styles = cls._FORMAT_STYLES.get(token_type, {})
            formats[token_type] = _make_format(color, **styles)
        return formats

    def highlightBlock(self, text: str) -> None:  # noqa: N802
        """Highlight one line of the document."""
        prev_state = self.previousBlockState()

        try:
            initial_state = State(prev_state) if prev_state != -1 else State.NORMAL
        except ValueError:
            initial_state = State.NORMAL

        # Drain the generator, capturing its return value (the final state).
        # Explicit next() calls preserve StopIteration.value (the state).
        gen = self.lexer.tokenize_line(text, initial_state)
        tokens: list[Token] = []
        final_state = initial_state
        try:
            while True:
                tokens.append(next(gen))
        except StopIteration as exc:
            if exc.value is not None:
                final_state = exc.value

        # Apply token formats
        for i, tok in enumerate(tokens):
            fmt = self.formats.get(tok.type, self.formats["ERROR"])

            # Special case: detect keys (string/timestamp immediately before colon)
            if tok.type in ("STRING", "TIMESTAMP"):
                if i + 1 < len(tokens) and tokens[i + 1].type == ":":
                    fmt = self.formats["KEY"]

            self.setFormat(tok.start, tok.end - tok.start, fmt)

        # Apply {{variable}} placeholders last (highest precedence)
        if "{{" in text:
            for match in _VARIABLE_PATTERN.finditer(text):
                self.setFormat(
                    match.start(), match.end() - match.start(), _VARIABLE_FMT
                )

        self.setCurrentBlockState(final_state.value)
