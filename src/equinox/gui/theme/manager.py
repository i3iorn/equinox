"""Theme application orchestration and stylesheet caching."""

from __future__ import annotations

from PyQt6.QtWidgets import QApplication

from .detection import system_is_dark
from .palettes import (
    is_dark_mode,
    palette_cache_key,
    resolve_palette,
    set_active_palette,
    validate_palettes,
)
from .settings import get_font_size, get_theme_mode, get_ui_font
from .stylesheet import build_stylesheet

_ss_cache: dict[tuple[str, int], str] = {}


def apply_theme(app: QApplication | None = None) -> None:
    """Re-apply the global stylesheet to the running QApplication."""
    qt_app = app if app is not None else QApplication.instance()
    if not isinstance(qt_app, QApplication):
        return

    mode = get_theme_mode()
    dark = system_is_dark(qt_app)
    palette = resolve_palette(mode, dark)
    set_active_palette(palette)

    validate_palettes()

    base_pt = get_font_size()
    qt_app.setFont(get_ui_font(base_pt))

    cache_key = (palette_cache_key(palette), base_pt)
    if cache_key not in _ss_cache:
        _ss_cache[cache_key] = build_stylesheet(base_pt, palette)
    qt_app.setStyleSheet(_ss_cache[cache_key])

    # Deferred import: equinox.gui.syntax_highlighter.base imports Colors
    # from this package, so importing it at module level here would be
    # circular. Already-open editors' highlighters otherwise keep whatever
    # colors were active when they were constructed — the QSS stylesheet
    # above re-applies instantly, but QSyntaxHighlighter formats do not.
    from equinox.gui.syntax_highlighter.base import notify_theme_changed

    notify_theme_changed()


def is_dark() -> bool:
    """Return True when current resolved theme is dark."""
    mode = get_theme_mode()
    return is_dark_mode(mode, system_is_dark())
