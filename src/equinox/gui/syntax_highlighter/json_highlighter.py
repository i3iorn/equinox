import re
from enum import Enum
from typing import Generator, List, NamedTuple

from PyQt6.QtGui import QSyntaxHighlighter

from equinox.gui.syntax_highlighter.base import _make_format, _variable_fmt, _VARIABLE_PATTERN
from equinox.gui.theme import Colors

__all__ = ["JsonHighlighter"]


# ----------------------------------------------------------------------
# Module-level compiled patterns
# Compiled once at import time — not per JsonLexer instance or per call.
# ----------------------------------------------------------------------

_NUMBER_RE = re.compile(r'-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?')

# ISO-8601 timestamp with optional fractional seconds and optional timezone
# (e.g. 2026-03-24T20:16:59, 2026-03-24T20:16:59.114824,
#  2026-03-24T20:16:59Z, 2026-03-24T20:16:59+02:00)
_TIMESTAMP_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?"
)

# Used for \uXXXX escape validation inside JSON strings.
_UNICODE_HEX_RE = re.compile(r'[0-9a-fA-F]{4}')

# Avoids allocating a fresh list on every iteration of the lexer inner loop.
_LITERALS = (("true", "TRUE"), ("false", "FALSE"), ("null", "NULL"))


# ----------------------------------------------------------------------
# Token + State
# ----------------------------------------------------------------------

class State(Enum):
    NORMAL = 0
    STRING = 1
    COMMENT_BLOCK = 2


class Token(NamedTuple):
    """Immutable lexer token.  NamedTuple is faster to create than a dataclass
    and is naturally immutable — tokens are never mutated after creation."""

    type: str
    start: int
    end: int
    value: str


# ----------------------------------------------------------------------
# Streaming JSON / JSONC Lexer
# ----------------------------------------------------------------------

class JsonLexer:
    def __init__(self, enable_comments: bool = True, enable_timestamps: bool = True):
        self.enable_comments = enable_comments
        self.enable_timestamps = enable_timestamps
        # Regex patterns are module-level constants; nothing to compile here.

    # --------------------------------------------------------------

    def tokenize_line(self, text: str, state: State) -> Generator[Token, None, State]:
        i = 0
        n = len(text)
        string_opened_here = False
        string_start_index: int = 0

        while i < n:
            ch = text[i]

            # ==========================================================
            # STRING STATE
            # ==========================================================

            if state == State.STRING:
                start = string_start_index if string_opened_here else i

                while i < n:
                    c = text[i]

                    if c == '"':
                        i += 1
                        state = State.NORMAL

                        content_start = (start + 1) if string_opened_here else start
                        value = text[content_start : i - 1]

                        if self.enable_timestamps and _TIMESTAMP_RE.fullmatch(value):
                            yield Token("TIMESTAMP", start, i, value)
                        else:
                            yield Token("STRING", start, i, value)

                        string_opened_here = False
                        break

                    elif c == '\\':
                        if i + 1 >= n:
                            yield Token("ERROR_STRING", i, i + 1, c)
                            i += 1
                            continue

                        esc = text[i + 1]

                        if esc == 'u':
                            # match(text, pos, endpos) avoids a text[i+2:i+6]
                            # substring and the i+5 < n boundary check —
                            # endpos handles the length guard automatically.
                            if _UNICODE_HEX_RE.match(text, i + 2, i + 6):
                                i += 6
                            else:
                                yield Token("ERROR_STRING", i, i + 2, text[i : i + 2])
                                i += 2
                        elif esc in '"\\/bfnrt':
                            i += 2
                        else:
                            yield Token("ERROR_STRING", i, i + 2, text[i : i + 2])
                            i += 2

                    else:
                        if ord(c) < 0x20:
                            yield Token("ERROR_STRING", i, i + 1, c)
                        i += 1

                else:
                    # Unterminated string — continues to the next line.
                    content_start = (start + 1) if string_opened_here else start
                    yield Token("STRING", start, n, text[content_start:])
                    return State.STRING

                continue

            # ==========================================================
            # BLOCK COMMENT STATE
            # ==========================================================

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

            # ==========================================================
            # NORMAL STATE
            # ==========================================================

            if ch.isspace():
                i += 1
                continue

            # JSONC comments
            if self.enable_comments:
                if text.startswith("//", i):
                    yield Token("COMMENT", i, n, text[i:])
                    return State.NORMAL

                if text.startswith("/*", i):
                    state = State.COMMENT_BLOCK
                    i += 2
                    continue

            # Structure characters
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

            # Number — use match(text, pos) to avoid a text[i:] substring.
            if ch == '-' or ch.isdigit():
                m = _NUMBER_RE.match(text, i)
                if m:
                    yield Token("NUMBER", i, m.end(), m.group(0))
                    i = m.end()
                else:
                    yield Token("ERROR_NUMBER", i, i + 1, ch)
                    i += 1
                continue

            # Literals (true / false / null)
            for lit, typ in _LITERALS:
                if text.startswith(lit, i):
                    yield Token(typ, i, i + len(lit), lit)
                    i += len(lit)
                    break
            else:
                yield Token("ERROR", i, i + 1, ch)
                i += 1

        return state


# ----------------------------------------------------------------------
# Highlighter
# ----------------------------------------------------------------------

class JsonHighlighter(QSyntaxHighlighter):
    def __init__(self, document):
        super().__init__(document)

        self.lexer = JsonLexer()
        self._var_fmt = _variable_fmt()

        self.formats = {
            "STRING":    _make_format(Colors.GREEN),
            "TIMESTAMP": _make_format(Colors.TEAL, italic=True),
            "NUMBER":    _make_format(Colors.PURPLE),

            "TRUE":  _make_format(Colors.AMBER, bold=True),
            "FALSE": _make_format(Colors.AMBER, bold=True),
            "NULL":  _make_format(Colors.AMBER, bold=True),

            "COMMENT": _make_format(Colors.GRAY, italic=True),

            "{": _make_format(Colors.FG_MUTED, bold=True),
            "}": _make_format(Colors.FG_MUTED, bold=True),
            "[": _make_format(Colors.FG_MUTED, bold=True),
            "]": _make_format(Colors.FG_MUTED, bold=True),

            ":": _make_format(Colors.FG_MUTED),
            ",": _make_format(Colors.FG_MUTED),

            "KEY": _make_format(Colors.BLUE, bold=True),

            "ERROR":        _make_format(Colors.RED, bold=True),
            "ERROR_STRING": _make_format(Colors.RED, underline=True),
            "ERROR_NUMBER": _make_format(Colors.RED, bold=True),
        }

    # --------------------------------------------------------------

    def highlightBlock(self, text: str) -> None:
        prev_state = self.previousBlockState()

        try:
            initial_state = State(prev_state) if prev_state != -1 else State.NORMAL
        except ValueError:
            initial_state = State.NORMAL

        # Drain the generator and capture its return value (the final state).
        # list() / a for-loop would silently discard StopIteration.value, so we
        # call next() explicitly to preserve it.
        gen = self.lexer.tokenize_line(text, initial_state)
        tokens: List[Token] = []
        final_state = initial_state
        try:
            while True:
                tokens.append(next(gen))
        except StopIteration as exc:
            if exc.value is not None:
                final_state = exc.value

        for i, tok in enumerate(tokens):
            fmt = self.formats.get(tok.type, self.formats["ERROR"])

            # Detect keys: a string immediately followed by a colon.
            if tok.type in ("STRING", "TIMESTAMP"):
                if i + 1 < len(tokens) and tokens[i + 1].type == ":":
                    fmt = self.formats["KEY"]

            self.setFormat(tok.start, tok.end - tok.start, fmt)

        # Apply {{variable}} placeholders last so they override other formats,
        # consistent with the rest of the highlighter suite.
        # Skip the regex scan entirely when no placeholder can be present.
        if "{{" in text:
            for match in _VARIABLE_PATTERN.finditer(text):
                self.setFormat(match.start(), match.end() - match.start(), self._var_fmt)

        self.setCurrentBlockState(final_state.value)
