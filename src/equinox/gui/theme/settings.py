"""Theme settings and font helpers backed by ``QSettings``."""

from __future__ import annotations

import sys

from PyQt6.QtCore import QSettings
from PyQt6.QtGui import QFont

from .palettes import THEME_MODES, THEME_SYSTEM

DEFAULT_FONT_SIZE = 9
DEFAULT_MONO_SIZE = 9
MIN_FONT_SIZE = 6
MAX_FONT_SIZE = 20

_SM_FONT_REDUCTION = 2
_SM_MIN_PT = 7

_PLATFORM_MONO_FONTS = {
    "win32": [
        "Consolas",
        "Courier New",
        "Lucida Console",
        "DejaVu Sans Mono",
        "Menlo",
        "Monaco",
        "monospace",
    ],
    "darwin": [
        "Menlo",
        "Monaco",
        "Consolas",
        "DejaVu Sans Mono",
        "Courier",
        "monospace",
    ],
    "linux": [
        "DejaVu Sans Mono",
        "Liberation Mono",
        "Consolas",
        "Menlo",
        "Monaco",
        "monospace",
    ],
}
_PLATFORM_UI_FONTS = {
    "win32": ["Segoe UI", "Arial", "Tahoma", "Verdana", "sans-serif"],
    "darwin": ["San Francisco", "Helvetica Neue", "Arial", "Verdana", "sans-serif"],
    "linux": ["DejaVu Sans", "Liberation Sans", "Arial", "Verdana", "sans-serif"],
}

_MONO_FONT_KEY = "appearance/mono_font_family"
_UI_FONT_KEY = "appearance/ui_font_family"


def _settings() -> QSettings:
    return QSettings("Equinox", "Equinox")


def _clamp_font_size(size: int) -> int:
    return max(MIN_FONT_SIZE, min(MAX_FONT_SIZE, size))


def _get_platform_fonts(kind: str) -> list[str]:
    platform = sys.platform
    if platform.startswith("win"):
        platform = "win32"
    elif platform == "darwin":
        platform = "darwin"
    else:
        platform = "linux"
    return (_PLATFORM_MONO_FONTS if kind == "mono" else _PLATFORM_UI_FONTS)[platform]


def get_font_size() -> int:
    value = _settings().value("appearance/font_size", DEFAULT_FONT_SIZE, type=int)
    return _clamp_font_size(value)


def save_font_size(size: int) -> None:
    _settings().setValue("appearance/font_size", _clamp_font_size(size))


def get_theme_mode() -> str:
    value = _settings().value("appearance/theme", THEME_SYSTEM, type=str)
    return value if value in THEME_MODES else THEME_SYSTEM


def save_theme_mode(mode: str) -> None:
    if mode not in THEME_MODES:
        mode = THEME_SYSTEM
    _settings().setValue("appearance/theme", mode)


def get_mono_font(size_override: int | None = None) -> QFont:
    size = size_override if size_override is not None else get_font_size()
    family = _settings().value(_MONO_FONT_KEY, None, type=str)
    if family:
        font = QFont(family, size)
    else:
        for font_name in _get_platform_fonts("mono"):
            font = QFont(font_name, size)
            if font.exactMatch():
                break
        else:
            font = QFont("monospace", size)
    font.setStyleHint(QFont.StyleHint.Monospace)
    return font


def get_ui_font(size_override: int | None = None) -> QFont:
    size = size_override if size_override is not None else get_font_size()
    family = _settings().value(_UI_FONT_KEY, None, type=str)
    if family:
        font = QFont(family, size)
    else:
        for font_name in _get_platform_fonts("ui"):
            font = QFont(font_name, size)
            if font.exactMatch():
                break
        else:
            font = QFont("sans-serif", size)
    font.setStyleHint(QFont.StyleHint.SansSerif)
    return font


def get_small_text_size(base_pt: int) -> int:
    return max(base_pt - _SM_FONT_REDUCTION, _SM_MIN_PT)
