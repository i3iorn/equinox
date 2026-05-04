"""Centralised theme and user-preference management for the Equinox GUI.

This package keeps the original ``equinox.gui.theme`` import surface while
splitting implementation into focused modules:
- ``palettes``: color palettes, mode constants, and ``Colors`` proxy
- ``settings``: QSettings-backed font/theme persistence and font helpers
- ``stylesheet``: stylesheet generation
- ``manager``: runtime application/caching orchestration
"""

from __future__ import annotations

from .manager import apply_theme, is_dark, _ss_cache
from .palettes import (
    Colors,
    THEME_DARK,
    THEME_LABELS,
    THEME_LIGHT,
    THEME_MODES,
    THEME_MUTED_DARK,
    THEME_OCEANIC,
    THEME_SYSTEM,
    validate_palettes,
)
from .settings import (
    DEFAULT_FONT_SIZE,
    DEFAULT_MONO_SIZE,
    MAX_FONT_SIZE,
    MIN_FONT_SIZE,
    get_font_size,
    get_mono_font,
    get_theme_mode,
    get_ui_font,
    save_font_size,
    save_theme_mode,
)

__all__ = [
    "Colors",
    "apply_theme",
    "get_font_size",
    "set_font_size",
    "get_mono_font",
    "get_ui_font",
    "get_theme_mode",
    "set_theme_mode",
    "is_dark",
    "THEME_SYSTEM",
    "THEME_LIGHT",
    "THEME_DARK",
    "THEME_MUTED_DARK",
    "THEME_OCEANIC",
    "THEME_MODES",
    "THEME_LABELS",
    "DEFAULT_FONT_SIZE",
    "DEFAULT_MONO_SIZE",
    "MIN_FONT_SIZE",
    "MAX_FONT_SIZE",
]


def set_font_size(size: int) -> None:
    """Persist the base font size and immediately re-apply the theme."""
    save_font_size(size)
    apply_theme()


def set_theme_mode(mode: str) -> None:
    """Persist the theme mode and immediately re-apply the theme."""
    save_theme_mode(mode)
    apply_theme()


# Run palette validation at package import so errors surface early.
validate_palettes()

