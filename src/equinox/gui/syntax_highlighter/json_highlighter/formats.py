from __future__ import annotations

from typing import Dict

from PyQt6.QtGui import QTextCharFormat

from equinox.gui.syntax_highlighter.base import _make_format
from equinox.gui.theme import Colors

__all__ = ["build_token_formats"]


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


def build_token_formats() -> Dict[str, QTextCharFormat]:
    """Build QTextCharFormat map from color and style specs."""
    formats: dict[str, QTextCharFormat] = {}
    for token_type, color in _FORMAT_MAP.items():
        styles = _FORMAT_STYLES.get(token_type, {})
        formats[token_type] = _make_format(color, **styles)
    return formats
