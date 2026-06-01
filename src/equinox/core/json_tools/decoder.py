from __future__ import annotations

import json
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Any

from ..exceptions import JsonParseError
from .lexer import JsonLexer
from .lexer import JsonLexerConfig


class JsonDecoder:
    """Decode JSON and JSONC text with strict error normalization."""

    def __init__(self, *, allow_comments: bool = False) -> None:
        self._allow_comments = allow_comments
        self.lexer = JsonLexer(JsonLexerConfig(allow_comments=allow_comments))

    def loads(self, text: str) -> Any:
        """Decode strict JSON text."""
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise JsonParseError(str(exc)) from exc

    def loads_strict(self, text: str) -> Any:
        """Decode JSON after rejecting lexer-level structural errors."""
        tokens = list(self.lexer.tokenize(text))
        if any(t.type.startswith("ERROR") for t in tokens):
            raise JsonParseError("Invalid JSON structure")
        payload = strip_json_comments(text) if self._allow_comments else text
        return self.loads(payload)

    def load_file(self, path: Path) -> Any:
        """Decode JSON from a UTF-8 encoded file."""
        return self.loads(path.read_text(encoding="utf-8"))

    def loads_jsonc(self, text: str) -> Any:
        """Decode JSON with JavaScript-style comments removed safely."""
        return self.loads(strip_json_comments(text))


@dataclass
class CommentStripState:
    text: str
    index: int = 0
    in_string: bool = False
    in_line_comment: bool = False
    in_block_comment: bool = False
    is_escaped: bool = False
    result: list[str] = field(default_factory=list)


def _peek(state: CommentStripState) -> str:
    if state.index + 1 < len(state.text):
        return state.text[state.index + 1]
    return ""


def _handle_line_comment(state: CommentStripState) -> None:
    ch = state.text[state.index]
    if ch in "\r\n":
        state.in_line_comment = False
        state.result.append(ch)
    state.index += 1


def _handle_block_comment(state: CommentStripState) -> None:
    ch = state.text[state.index]
    nxt = _peek(state)
    if ch == "*" and nxt == "/":
        state.in_block_comment = False
        state.index += 2
        return
    if ch in "\r\n":
        state.result.append(ch)
    state.index += 1


def _handle_string_mode(state: CommentStripState) -> None:
    ch = state.text[state.index]
    state.result.append(ch)

    if state.is_escaped:
        state.is_escaped = False
    elif ch == "\\":
        state.is_escaped = True
    elif ch == '"':
        state.in_string = False

    state.index += 1


def _handle_comment_start(state: CommentStripState) -> bool:
    """Return True if a comment start was handled."""
    ch = state.text[state.index]
    nxt = _peek(state)

    if ch == "/" and nxt == "/":
        state.result.pop()  # remove the '/'
        state.in_line_comment = True
        state.index += 2
        return True

    if ch == "/" and nxt == "*":
        state.result.pop()  # remove the '/'
        state.in_block_comment = True
        state.index += 2
        return True

    return False


def _handle_normal_char(state: CommentStripState) -> None:
    ch = state.text[state.index]
    state.result.append(ch)

    if ch == '"':
        state.in_string = True
        state.index += 1
        return

    if _handle_comment_start(state):
        return

    state.index += 1


def strip_json_comments(text: str) -> str:
    """Remove line and block comments while preserving JSON string contents."""
    if not isinstance(text, str):
        raise TypeError("text must be a string")

    state = CommentStripState(text=text)

    while state.index < len(state.text):
        if state.in_line_comment:
            _handle_line_comment(state)
            continue

        if state.in_block_comment:
            _handle_block_comment(state)
            continue

        if state.in_string:
            _handle_string_mode(state)
            continue

        _handle_normal_char(state)

    if state.in_block_comment:
        raise JsonParseError("Unterminated block comment")

    if state.in_string:
        raise JsonParseError("Unterminated string literal")

    return "".join(state.result)
