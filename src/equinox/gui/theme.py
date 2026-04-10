"""Centralised theme and user-preference management for the Equinox GUI.

All colours, font sizes, and stylesheet rules live here so that panels
never hard-code presentation values.  User preferences are persisted
via ``QSettings`` (INI file on all platforms).

Supports **Light**, **Dark**, and **System** (auto-detect) modes.
"""

from __future__ import annotations

import sys

from PyQt6.QtCore import QSettings
from PyQt6.QtGui import QFont, QPalette
from PyQt6.QtWidgets import QApplication

# ── Colour palettes ──────────────────────────────────────────────────────────

_LIGHT: dict[str, str] = {
    # Status / method badges
    "GREEN":      "#1a7f37",
    "AMBER":      "#9a6700",
    "RED":        "#cf222e",
    "BLUE":       "#0550ae",
    "PURPLE":     "#8250df",
    "MUTED":      "#656d76",
    "CYAN":       "#006b75",
    "GRAY":       "#57606a",
    "TEAL":       "#008080",
    # Surfaces — slightly off-white to reduce eye strain
    "BG":         "#f7f7f8",
    "BG_ALT":     "#ededef",
    "BORDER":     "#d1d9e0",
    "BORDER_FCS": "#0969da",
    # Text
    "FG":         "#1f2328",
    "FG_MUTED":   "#656d76",
    "FG_SUBTLE":  "#848d97",
    # Selection / highlight
    "SELECTION":  "#dceefb",
    "SEL_TEXT":   "#b6d7f8",
    "HIGHLIGHT":  "#fff3a3",
    # Send / cancel button hover
    "SEND_HOVER": "#0860ca",
}

_DARK: dict[str, str] = {
    # Status / method badges — muted but still readable on dark backgrounds
    "GREEN":      "#2da44e",
    "AMBER":      "#b8860b",
    "RED":        "#e0484b",
    "BLUE":       "#4d8ed4",
    "PURPLE":     "#9a7bdb",
    "MUTED":      "#8b949e",
    "CYAN":       "#39c5cf",
    "GRAY":       "#6e7681",
    "TEAL":       "#39c5cf",
    # Surfaces
    "BG":         "#0d1117",
    "BG_ALT":     "#161b22",
    "BORDER":     "#30363d",
    "BORDER_FCS": "#4d8ed4",
    # Text
    "FG":         "#e6edf3",
    "FG_MUTED":   "#8b949e",
    "FG_SUBTLE":  "#6e7681",
    # Selection / highlight
    "SELECTION":  "#1f3347",
    "SEL_TEXT":   "#264f78",
    "HIGHLIGHT":  "#5a4a28",
    # Send / cancel button hover
    "SEND_HOVER": "#6ab5eb",
}


# ── Dynamic Colors proxy ─────────────────────────────────────────────────────

class _ColorProxy:
    """Attribute-access proxy that reads from the active palette.

    All panels use ``Colors.GREEN`` etc. — this object makes those lookups
    return the correct hex string for the currently-active theme.
    """

    @property
    def SUCCESS(self) -> str:
        return _active["GREEN"]

    @property
    def WARNING(self) -> str:
        return _active["AMBER"]

    @property
    def ERROR(self) -> str:
        return _active["RED"]

    @property
    def INFO(self) -> str:
        return _active["BLUE"]

    @property
    def METHOD(self) -> dict[str, str]:
        p = _active
        return {
            "GET":     p["GREEN"],
            "POST":    p["AMBER"],
            "PUT":     p["BLUE"],
            "PATCH":   p["PURPLE"],
            "DELETE":  p["RED"],
            "HEAD":    p["MUTED"],
            "OPTIONS": p["MUTED"],
        }

    def __getattr__(self, name: str) -> str:
        try:
            return _active[name]
        except KeyError:
            raise AttributeError(f"Colors has no attribute {name!r}")


Colors = _ColorProxy()

__all__ = [
    "Colors",
    "apply_theme",
    "get_font_size", "set_font_size",
    "get_mono_font", "get_ui_font",
    "get_theme_mode", "set_theme_mode",
    "is_dark",
    "THEME_SYSTEM", "THEME_LIGHT", "THEME_DARK", "THEME_MODES", "THEME_LABELS",
    "DEFAULT_FONT_SIZE", "DEFAULT_MONO_SIZE", "MIN_FONT_SIZE", "MAX_FONT_SIZE",
]

# The active palette dict — switched by apply_theme()
_active: dict[str, str] = dict(_LIGHT)


# ── Theme mode constants ─────────────────────────────────────────────────────

THEME_SYSTEM = "system"
THEME_LIGHT  = "light"
THEME_DARK   = "dark"
THEME_MODES  = (THEME_SYSTEM, THEME_LIGHT, THEME_DARK)
THEME_LABELS = {"system": "System", "light": "Light", "dark": "Dark"}


# ── Defaults ─────────────────────────────────────────────────────────────────

DEFAULT_FONT_SIZE  = 9
DEFAULT_MONO_SIZE  = 9
MIN_FONT_SIZE      = 6
MAX_FONT_SIZE      = 20


# ── Settings persistence ────────────────────────────────────────────────────

def _settings() -> QSettings:
    return QSettings("Equinox", "Equinox")


def get_font_size() -> int:
    """Return the user-chosen base font size (pt)."""
    s = _settings()
    val = s.value("appearance/font_size", DEFAULT_FONT_SIZE, type=int)
    return max(MIN_FONT_SIZE, min(MAX_FONT_SIZE, val))


def set_font_size(size: int) -> None:
    """Persist the base font size and immediately re-apply the theme."""
    size = max(MIN_FONT_SIZE, min(MAX_FONT_SIZE, size))
    _settings().setValue("appearance/font_size", size)
    apply_theme()


def get_theme_mode() -> str:
    """Return the persisted theme mode (``system``, ``light``, or ``dark``)."""
    val = _settings().value("appearance/theme", THEME_SYSTEM, type=str)
    return val if val in THEME_MODES else THEME_SYSTEM


def set_theme_mode(mode: str) -> None:
    """Persist the theme mode and immediately re-apply the theme."""
    if mode not in THEME_MODES:
        mode = THEME_SYSTEM
    _settings().setValue("appearance/theme", mode)
    apply_theme()


# ── Font helpers ─────────────────────────────────────────────────────────────

def get_mono_font(size_override: int | None = None) -> QFont:
    """Return a monospaced QFont at the user-chosen (or overridden) size."""
    sz = size_override if size_override is not None else get_font_size()
    f = QFont("Consolas", sz)
    f.setStyleHint(QFont.StyleHint.Monospace)
    return f


def get_ui_font(size_override: int | None = None) -> QFont:
    """Return the default UI QFont at the user-chosen (or overridden) size."""
    sz = size_override if size_override is not None else get_font_size()
    f = QFont("Segoe UI", sz)
    f.setStyleHint(QFont.StyleHint.SansSerif)
    return f


# ── System dark-mode detection ───────────────────────────────────────────────

def _system_is_dark() -> bool:
    """Best-effort detection of the OS dark-mode preference."""
    # Windows registry check (most reliable on Windows)
    if sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
            ) as key:
                val, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return val == 0
        except Exception:
            pass

    # Fallback: Qt palette luminance check
    app = QApplication.instance()
    if isinstance(app, QApplication):
        palette = app.palette()
        window_color = palette.color(QPalette.ColorRole.Window)
        return window_color.lightnessF() < 0.5

    return False


def _resolve_dark() -> bool:
    """Return True when the active palette should be dark."""
    mode = get_theme_mode()
    if mode == THEME_DARK:
        return True
    if mode == THEME_LIGHT:
        return False
    return _system_is_dark()


def is_dark() -> bool:
    """Return True if the current resolved theme is dark."""
    return _resolve_dark()


# ── Master stylesheet ───────────────────────────────────────────────────────

def _build_stylesheet(base_pt: int) -> str:
    """Generate the application-wide QSS string."""
    C = _active
    sm = max(base_pt - 2, 7)

    return f"""
    /* ── Global ─────────────────────────────────────────── */
    * {{
        font-size: {base_pt}pt;
    }}
    QMainWindow, QDialog {{
        background: {C["BG"]};
        color: {C["FG"]};
    }}

    /* ── Buttons ────────────────────────────────────────── */
    QPushButton {{
        padding: 4px 14px;
        border: 1px solid {C["BORDER"]};
        border-radius: 4px;
        background: {C["BG"]};
        color: {C["FG"]};
        min-height: 24px;
    }}
    QPushButton:hover {{
        background: {C["BG_ALT"]};
        border-color: {C["FG_MUTED"]};
    }}
    QPushButton:pressed {{
        background: {C["BORDER"]};
    }}
    QPushButton:disabled {{
        color: {C["FG_SUBTLE"]};
        border-color: {C["BORDER"]};
    }}
    QPushButton#sendBtn {{
        background: {C["BLUE"]};
        color: #ffffff;
        border: 1px solid {C["BLUE"]};
        font-weight: bold;
    }}
    QPushButton#sendBtn:hover {{
        background: {C["SEND_HOVER"]};
        border-color: {C["SEND_HOVER"]};
    }}
    QPushButton#sendBtn:disabled {{
        background: {C["FG_SUBTLE"]};
        border-color: {C["FG_SUBTLE"]};
        color: {C["BG"]};
    }}
    QPushButton#cancelBtn {{
        background: {C["RED"]};
        color: #ffffff;
        border: 1px solid {C["RED"]};
    }}

    /* ── Inputs ─────────────────────────────────────────── */
    QLineEdit, QTextEdit, QPlainTextEdit {{
        border: 1px solid {C["BORDER"]};
        border-radius: 4px;
        padding: 4px 6px;
        background: {C["BG"]};
        color: {C["FG"]};
        selection-background-color: {C["SEL_TEXT"]};
    }}
    QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {{
        border-color: {C["BORDER_FCS"]};
    }}
    QComboBox {{
        border: 1px solid {C["BORDER"]};
        border-radius: 4px;
        padding: 3px 8px;
        background: {C["BG"]};
        color: {C["FG"]};
        min-height: 24px;
    }}
    QComboBox:focus {{
        border-color: {C["BORDER_FCS"]};
    }}
    QComboBox::drop-down {{
        border: none;
        width: 20px;
    }}
    QComboBox QAbstractItemView {{
        background: {C["BG"]};
        color: {C["FG"]};
        border: 1px solid {C["BORDER"]};
        selection-background-color: {C["SELECTION"]};
    }}

    /* ── Tabs ───────────────────────────────────────────── */
    QTabWidget::pane {{
        border: 1px solid {C["BORDER"]};
        border-top: none;
        background: {C["BG"]};
    }}
    QTabBar::tab {{
        padding: 6px 14px;
        border: 1px solid transparent;
        border-bottom: 2px solid transparent;
        margin-right: 2px;
        color: {C["FG_MUTED"]};
        font-size: {sm}pt;
    }}
    QTabBar::tab:selected {{
        color: {C["FG"]};
        border-bottom: 2px solid {C["BLUE"]};
    }}
    QTabBar::tab:hover:!selected {{
        color: {C["FG"]};
        border-bottom: 2px solid {C["BORDER"]};
    }}

    /* ── Tables ─────────────────────────────────────────── */
    QTableWidget, QTableView {{
        border: 1px solid {C["BORDER"]};
        gridline-color: {C["BORDER"]};
        background: {C["BG"]};
        alternate-background-color: {C["BG_ALT"]};
        selection-background-color: {C["SELECTION"]};
        color: {C["FG"]};
    }}
    QHeaderView::section {{
        background: {C["BG_ALT"]};
        border: none;
        border-bottom: 1px solid {C["BORDER"]};
        border-right: 1px solid {C["BORDER"]};
        padding: 4px 8px;
        font-weight: bold;
        font-size: {sm}pt;
        color: {C["FG_MUTED"]};
    }}

    /* ── Tree / List ───────────────────────────────────── */
    QTreeWidget, QListWidget {{
        border: 1px solid {C["BORDER"]};
        background: {C["BG"]};
        alternate-background-color: {C["BG_ALT"]};
        color: {C["FG"]};
        outline: none;
    }}
    QTreeWidget::item, QListWidget::item {{
        padding: 3px 4px;
    }}
    QTreeWidget::item:selected, QListWidget::item:selected {{
        background: {C["SELECTION"]};
        color: {C["FG"]};
    }}
    QTreeWidget::item:hover, QListWidget::item:hover {{
        background: {C["BG_ALT"]};
    }}

    /* ── Splitter handles ──────────────────────────────── */
    QSplitter::handle {{
        background: {C["BORDER"]};
    }}
    QSplitter::handle:horizontal {{
        width: 5px;
    }}
    QSplitter::handle:vertical {{
        height: 5px;
    }}

    /* ── Scrollbars (slim) ─────────────────────────────── */
    QScrollBar:vertical {{
        width: 8px;
        background: transparent;
    }}
    QScrollBar::handle:vertical {{
        background: {C["BORDER"]};
        border-radius: 4px;
        min-height: 24px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: {C["FG_SUBTLE"]};
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0px;
    }}
    QScrollBar:horizontal {{
        height: 8px;
        background: transparent;
    }}
    QScrollBar::handle:horizontal {{
        background: {C["BORDER"]};
        border-radius: 4px;
        min-width: 24px;
    }}
    QScrollBar::handle:horizontal:hover {{
        background: {C["FG_SUBTLE"]};
    }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
        width: 0px;
    }}

    /* ── Status bar ────────────────────────────────────── */
    QStatusBar {{
        background: {C["BG_ALT"]};
        border-top: 1px solid {C["BORDER"]};
        font-size: {sm}pt;
        color: {C["FG_MUTED"]};
    }}

    /* ── Menu bar ──────────────────────────────────────── */
    QMenuBar {{
        background: {C["BG_ALT"]};
        border-bottom: 1px solid {C["BORDER"]};
        padding: 2px;
        color: {C["FG"]};
    }}
    QMenuBar::item {{
        padding: 4px 10px;
        border-radius: 3px;
    }}
    QMenuBar::item:selected {{
        background: {C["BORDER"]};
    }}
    QMenu {{
        background: {C["BG"]};
        color: {C["FG"]};
        border: 1px solid {C["BORDER"]};
        padding: 4px 0;
    }}
    QMenu::item {{
        padding: 5px 28px 5px 12px;
    }}
    QMenu::item:selected {{
        background: {C["SELECTION"]};
    }}
    QMenu::separator {{
        height: 1px;
        background: {C["BORDER"]};
        margin: 4px 8px;
    }}

    /* ── Group box ────────────────────────────────────── */
    QGroupBox {{
        border: 1px solid {C["BORDER"]};
        border-radius: 4px;
        margin-top: 8px;
        padding-top: 16px;
        color: {C["FG"]};
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        left: 12px;
        padding: 0 4px;
    }}

    /* ── Checkbox ──────────────────────────────────────── */
    QCheckBox {{
        spacing: 6px;
        font-size: {sm}pt;
        color: {C["FG_MUTED"]};
    }}

    /* ── Tooltips ──────────────────────────────────────── */
    QToolTip {{
        background: {C["FG"]};
        color: {C["BG"]};
        border: none;
        padding: 4px 8px;
        font-size: {sm}pt;
    }}

    /* ── Form layout labels ───────────────────────────── */
    QFormLayout QLabel {{
        color: {C["FG_MUTED"]};
    }}

    /* ── Muted helper-text labels ─────────────────────── */
    QLabel#mutedLabel {{
        color: {C["FG_MUTED"]};
        font-size: {sm}pt;
    }}

    /* ── Slider ─────────────────────────────────────── */
    QSlider::groove:horizontal {{
        height: 4px;
        background: {C["BORDER"]};
        border-radius: 2px;
    }}
    QSlider::handle:horizontal {{
        width: 14px;
        height: 14px;
        margin: -5px 0;
        background: {C["BLUE"]};
        border-radius: 7px;
    }}
    QSlider::handle:horizontal:hover {{
        background: {C["SEND_HOVER"]};
    }}

    /* ── SpinBox ─────────────────────────────────────── */
    QSpinBox {{
        border: 1px solid {C["BORDER"]};
        border-radius: 4px;
        padding: 2px 4px;
        background: {C["BG"]};
        color: {C["FG"]};
    }}
    QSpinBox:focus {{
        border-color: {C["BORDER_FCS"]};
    }}

    /* ── Dialog buttons ─────────────────────────────── */
    QDialogButtonBox QPushButton {{
        min-width: 80px;
    }}
    """


# ── Public API ───────────────────────────────────────────────────────────────

# Stylesheet cache keyed by (is_dark, base_pt).
# _build_stylesheet produces a ~450-line f-string that Qt re-parses in full
# on every setStyleSheet() call.  Caching avoids redundant work when the
# theme is re-applied without any settings change (e.g. on window creation).
# The cache has at most 2 × (MAX_FONT_SIZE − MIN_FONT_SIZE + 1) ≈ 30 entries.
_ss_cache: dict[tuple[bool, int], str] = {}


def apply_theme(app: QApplication | None = None) -> None:
    """(Re-)apply the global stylesheet to the running QApplication.

    Resolves the effective palette (light/dark/system), updates the
    ``Colors`` proxy, sets the application font, and installs the
    generated stylesheet.

    The stylesheet string is cached by ``(is_dark, base_pt)`` so repeated
    calls with unchanged settings avoid the cost of rebuilding and re-parsing
    a ~450-line QSS document.
    """
    global _active

    if app is None:
        app = QApplication.instance()
    if app is None:
        return

    # Resolve effective palette
    dark = _resolve_dark()
    _active = dict(_DARK if dark else _LIGHT)

    base_pt = get_font_size()
    app.setFont(get_ui_font(base_pt))

    cache_key = (dark, base_pt)
    if cache_key not in _ss_cache:
        _ss_cache[cache_key] = _build_stylesheet(base_pt)
    app.setStyleSheet(_ss_cache[cache_key])

