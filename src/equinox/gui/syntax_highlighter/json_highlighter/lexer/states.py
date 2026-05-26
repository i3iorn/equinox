from __future__ import annotations

from enum import Enum


class State(Enum):
    """Lexer state for multi-line context tracking."""

    NORMAL = 0
    STRING = 1
    COMMENT_BLOCK = 2
