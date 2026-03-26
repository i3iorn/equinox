import re
from dataclasses import dataclass
from enum import Enum
from typing import Iterator, List, Optional

from PyQt6.QtGui import QSyntaxHighlighter

from equinox.gui.syntax_highlighter.base import _make_format
from equinox.gui.theme import Colors


# ----------------------------------------------------------------------
# Token + State
# ----------------------------------------------------------------------

class State(Enum):
    NORMAL = 0
    STRING = 1
    COMMENT_BLOCK = 2


@dataclass
class Token:
    type: str
    start: int
    end: int
    value: str


# ----------------------------------------------------------------------
# Streaming JSON / JSONC Lexer
# ----------------------------------------------------------------------

class JsonLexer:
    def __init__(self, enable_comments=True, enable_timestamps=True):
        self.enable_comments = enable_comments
        self.enable_timestamps = enable_timestamps

        self.number_re = re.compile(
            r'-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?'
        )

        # ISO-8601 timestamp with optional fractional seconds and optional
        # timezone (e.g. 2026-03-24T20:16:59, 2026-03-24T20:16:59.114824,
        # 2026-03-24T20:16:59Z, 2026-03-24T20:16:59+02:00)
        self.timestamp_re = re.compile(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?"
        )

    # --------------------------------------------------------------

    def tokenize_line(self, text: str, state: State) -> Iterator[Token]:
        i = 0
        n = len(text)
        # When a string begins on this line we record the opening-quote
        # index so the emitted token can include the leading quote. If the
        # tokenizer is started in State.STRING (continuation from the
        # previous line), we must NOT include a leading quote for this
        # line because it is not present here.
        string_opened_here = False
        string_start_index: Optional[int] = None

        while i < n:
            ch = text[i]

            # ==========================================================
            # STRING STATE
            # ==========================================================

            if state == State.STRING:
                # If the string was opened on this same line we include the
                # leading quote in the emitted token range; otherwise the
                # opening quote belongs to a previous line and should not be
                # included here.
                start = string_start_index if string_opened_here and string_start_index is not None else i

                while i < n:
                    c = text[i]

                    if c == '"':
                        # include the closing quote in the token range
                        i += 1
                        state = State.NORMAL

                        # Content without surrounding quotes for value-based
                        # checks (e.g. timestamp matching)
                        content_start = (start + 1) if (string_opened_here and string_start_index is not None) else start
                        content_end = i - 1
                        value = text[content_start:content_end]

                        if self.enable_timestamps and self.timestamp_re.fullmatch(value):
                            yield Token("TIMESTAMP", start, i, value)
                        else:
                            yield Token("STRING", start, i, value)

                        # reset the per-line opener flag
                        string_opened_here = False
                        string_start_index = None
                        break

                    elif c == '\\':
                        if i + 1 >= n:
                            yield Token("ERROR_STRING", i, i + 1, c)
                            i += 1
                            continue

                        esc = text[i + 1]

                        if esc == 'u':
                            if i + 5 < n and re.match(r'[0-9a-fA-F]{4}', text[i+2:i+6]):
                                i += 6
                            else:
                                yield Token("ERROR_STRING", i, i + 2, text[i:i+2])
                                i += 2
                        elif esc in '"\\/bfnrt':
                            i += 2
                        else:
                            yield Token("ERROR_STRING", i, i + 2, text[i:i+2])
                            i += 2

                    else:
                        if ord(c) < 0x20:
                            yield Token("ERROR_STRING", i, i + 1, c)
                        i += 1

                else:
                    # unterminated string (continues to next line). Include
                    # the leading quote if it was opened on this line.
                    content_start = (start + 1) if (string_opened_here and string_start_index is not None) else start
                    value = text[content_start:]
                    yield Token("STRING", start, n, value)
                    return State.STRING

                continue

            # ==========================================================
            # BLOCK COMMENT STATE
            # ==========================================================

            if state == State.COMMENT_BLOCK:
                start = i

                while i < n:
                    if text[i:i+2] == "*/":
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

            # structure
            if ch in "{}[]:,":
                yield Token(ch, i, i + 1, ch)
                i += 1
                continue

            # string start
            if ch == '"':
                # mark that the string opened on this line so we include
                # the leading quote in the emitted token range
                string_opened_here = True
                string_start_index = i
                state = State.STRING
                i += 1
                continue

            # number
            if ch == '-' or ch.isdigit():
                match = self.number_re.match(text[i:])
                if match:
                    val = match.group(0)
                    yield Token("NUMBER", i, i + len(val), val)
                    i += len(val)
                    continue
                else:
                    yield Token("ERROR_NUMBER", i, i + 1, ch)
                    i += 1
                    continue

            # literals
            for lit, typ in [("true", "TRUE"), ("false", "FALSE"), ("null", "NULL")]:
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

        self.formats = {
            "STRING": _make_format(Colors.GREEN),
            "UPPER_STRING": _make_format(Colors.GREEN, bold=True),
            "TIMESTAMP": _make_format(Colors.TEAL, italic=True),
            "NUMBER": _make_format(Colors.PURPLE),

            "TRUE": _make_format(Colors.AMBER, bold=True),
            "FALSE": _make_format(Colors.AMBER, bold=True),
            "NULL": _make_format(Colors.AMBER, bold=True),

            "COMMENT": _make_format(Colors.GRAY, italic=True),

            "{": _make_format(Colors.FG_MUTED, bold=True),
            "}": _make_format(Colors.FG_MUTED, bold=True),
            "[": _make_format(Colors.FG_MUTED, bold=True),
            "]": _make_format(Colors.FG_MUTED, bold=True),

            ":": _make_format(Colors.FG_MUTED),
            ",": _make_format(Colors.FG_MUTED),

            "KEY": _make_format(Colors.BLUE, bold=True),

            "ERROR": _make_format(Colors.RED, bold=True),
            "ERROR_STRING": _make_format(Colors.RED, underline=True),
            "ERROR_NUMBER": _make_format(Colors.RED, bold=True),
        }

    # --------------------------------------------------------------

    def highlightBlock(self, text: str):
        prev_state = self.previousBlockState()

        state = State(prev_state) if prev_state != -1 else State.NORMAL

        tokens: List[Token] = list(self.lexer.tokenize_line(text, state))

        for i, tok in enumerate(tokens):
            fmt = self.formats.get(tok.type, self.formats["ERROR"])

            # Detect keys (string followed by colon)
            if tok.type in ("STRING", "TIMESTAMP"):
                if i + 1 < len(tokens) and tokens[i + 1].type == ":":
                    fmt = self.formats["KEY"]

            self.setFormat(tok.start, tok.end - tok.start, fmt)

        self.setCurrentBlockState(state.value)
