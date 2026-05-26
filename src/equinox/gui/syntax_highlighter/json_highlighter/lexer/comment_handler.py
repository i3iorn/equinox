from __future__ import annotations

from typing import Optional, Tuple

from .patterns import Token


def try_line_comment(text: str, index: int) -> Optional[Token]:
    """Return a COMMENT token if // starts at index."""
    if text.startswith("//", index):
        return Token("COMMENT", index, len(text), text[index:])
    return None


def try_block_comment_start(text: str, index: int) -> bool:
    """Return True if /* starts at index."""
    return text.startswith("/*", index)


def consume_block_comment(text: str, index: int) -> Tuple[int, Token, bool]:
    """Consume a /* ... */ block comment.

    Returns:
        new_index: Position after the consumed text.
        token: COMMENT token for the consumed range.
        is_closed: True if '*/' was found, False if comment continues.
    """
    start = index
    length = len(text)

    while index < length:
        if text.startswith("*/", index):
            end = index + 2
            return end, Token("COMMENT", start, end, text[start:end]), True
        index += 1

    return length, Token("COMMENT", start, length, text[start:]), False
