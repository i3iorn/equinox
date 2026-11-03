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

# UI Density modes and their padding/font scaling multipliers.
DENSITY_COMPACT = "compact"
DENSITY_COMFORTABLE = "comfortable"
DENSITY_SPACIOUS = "spacious"

DENSITY_MODES = (DENSITY_COMPACT, DENSITY_COMFORTABLE, DENSITY_SPACIOUS)
DENSITY_LABELS = {
    DENSITY_COMPACT: "Compact",
    DENSITY_COMFORTABLE: "Comfortable",
    DENSITY_SPACIOUS: "Spacious",
}
# Multiplier applied to base padding (relative to Comfortable = 1.0).
DENSITY_PADDING_SCALE = {
    DENSITY_COMPACT: 0.75,
    DENSITY_COMFORTABLE: 1.0,
    DENSITY_SPACIOUS: 1.35,
}
# Multiplier applied to base font size.
DENSITY_FONT_SCALE = {
    DENSITY_COMPACT: 0.9,
    DENSITY_COMFORTABLE: 1.0,
    DENSITY_SPACIOUS: 1.1,
}

_DENSITY_KEY = "appearance/ui_density"


def get_ui_density() -> str:
    value = _settings().value(_DENSITY_KEY, DENSITY_COMFORTABLE, type=str)
    return value if value in DENSITY_MODES else DENSITY_COMFORTABLE


def save_ui_density(density: str) -> None:
    if density not in DENSITY_MODES:
        density = DENSITY_COMFORTABLE
    settings = _settings()
    settings.setValue(_DENSITY_KEY, density)
    settings.sync()


def get_density_padding_scale() -> float:
    return DENSITY_PADDING_SCALE[get_ui_density()]


def get_density_font_scale() -> float:
    return DENSITY_FONT_SCALE[get_ui_density()]


# ── Native window decorations ──────────────────────────────────────────────
_NATIVE_TITLE_BAR_KEY = "window/native_title_bar"


def get_use_native_title_bar() -> bool:
    value = _settings().value(_NATIVE_TITLE_BAR_KEY, False, type=bool)
    return bool(value)


def save_use_native_title_bar(enabled: bool) -> None:
    settings = _settings()
    settings.setValue(_NATIVE_TITLE_BAR_KEY, bool(enabled))
    settings.sync()


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
    """Return the shared GUI settings handle.

    Delegates rather than constructing its own: this module and ui_common
    both addressed the same ("Equinox", "Equinox") store, so a write through
    one could be unflushed when the other read it.
    """
    from equinox.gui.ui_common import get_gui_settings

    return get_gui_settings()


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
    settings = _settings()
    settings.setValue("appearance/font_size", _clamp_font_size(size))
    # Flush now: the value is read straight back (zoom in/out re-reads it to
    # compute the next step), and callers in other processes should see it.
    settings.sync()


def get_theme_mode() -> str:
    value = _settings().value("appearance/theme", THEME_SYSTEM, type=str)
    return value if value in THEME_MODES else THEME_SYSTEM


def save_theme_mode(mode: str) -> None:
    if mode not in THEME_MODES:
        mode = THEME_SYSTEM
    settings = _settings()
    settings.setValue("appearance/theme", mode)
    settings.sync()


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
