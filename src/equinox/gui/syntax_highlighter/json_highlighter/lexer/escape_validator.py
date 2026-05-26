from __future__ import annotations

from typing import Optional, Tuple

from .patterns import JSON_ESCAPE_CHARS, UNICODE_HEX_RE, Token


def validate_escape(text: str, index: int) -> Tuple[int, Optional[Token]]:
    """Validate a JSON escape sequence starting at index.

    Returns:
        new_index: Position after the escape sequence.
        error_token: ERROR_STRING token if invalid, otherwise None.
    """
    length = len(text)
    if index + 1 >= length:
        return index + 1, Token("ERROR_STRING", index, index + 1, text[index])

    esc = text[index + 1]

    if esc == "u":
        if UNICODE_HEX_RE.match(text, index + 2, index + 6):
            return index + 6, None
        return index + 2, Token("ERROR_STRING", index, index + 2, text[index : index + 2])

    if esc in JSON_ESCAPE_CHARS:
        return index + 2, None

    return index + 2, Token("ERROR_STRING", index, index + 2, text[index : index + 2])
