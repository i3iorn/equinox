from __future__ import annotations

from equinox.gui.syntax_highlighter.base import _make_format
from equinox.gui.theme import Colors
from PyQt6.QtGui import QTextCharFormat

__all__ = ["build_token_formats"]


# Color *keys* (not resolved values) — Colors.* must be read inside
# build_token_formats() at call time, not here at module-import time.
# Colors.GREEN etc. read live from the active theme palette, but a value
# captured once into a module-level dict would freeze whichever theme was
# active at import and never update on a later theme switch.
_FORMAT_COLOR_KEYS: dict[str, str] = {
    "STRING": "GREEN",
    "TIMESTAMP": "TEAL",
    "NUMBER": "PURPLE",
    "TRUE": "AMBER",
    "FALSE": "AMBER",
    "NULL": "AMBER",
    "COMMENT": "FG_MUTED",  # matches python/xml/yaml highlighters' comment color
    "{": "FG_MUTED",
    "}": "FG_MUTED",
    "[": "FG_MUTED",
    "]": "FG_MUTED",
    ":": "FG_MUTED",
    ",": "FG_MUTED",
    "KEY": "BLUE",
    "ERROR": "RED",
    "ERROR_STRING": "RED",
    "ERROR_NUMBER": "RED",
}

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


def build_token_formats() -> dict[str, QTextCharFormat]:
    """Build QTextCharFormat map from color and style specs.

    Reads ``Colors.*`` fresh on every call so a rebuild after a theme
    switch (see ``JsonHighlighter.refresh_theme()``) actually picks up the
    new palette.
    """
    formats: dict[str, QTextCharFormat] = {}
    for token_type, color_key in _FORMAT_COLOR_KEYS.items():
        styles = _FORMAT_STYLES.get(token_type, {})
        formats[token_type] = _make_format(getattr(Colors, color_key), **styles)
    return formats
