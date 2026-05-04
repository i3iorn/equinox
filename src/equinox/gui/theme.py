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

__all__ = [
    "Colors",
    "apply_theme",
    "get_font_size", "set_font_size",
    "get_mono_font", "get_ui_font",
    "get_theme_mode", "set_theme_mode",
    "is_dark",
    "THEME_SYSTEM", "THEME_LIGHT", "THEME_DARK", "THEME_MUTED_DARK", "THEME_OCEANIC", "THEME_MODES", "THEME_LABELS",
    "DEFAULT_FONT_SIZE", "DEFAULT_MONO_SIZE", "MIN_FONT_SIZE", "MAX_FONT_SIZE",
]

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
    # Text (WCAG AA contrast ratios maintained)
    "FG":         "#1f2328",
    "FG_MUTED":   "#4d5561",  # Improved: darker for better contrast, but still readable
    "FG_SUBTLE":  "#6e7681",  # Improved: better contrast for secondary text
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
    "BLUE":       "#6daef4",
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
    # Text (WCAG AA contrast ratios: all >4.5:1)
    "FG":         "#e6edf3",
    "FG_MUTED":   "#b0b9c3",  # Improved: significantly lighter for ~7:1 contrast on BG
    "FG_SUBTLE":  "#8b949e",  # Improved: better contrast than before
    # Selection / highlight
    "SELECTION":  "#1f3347",
    "SEL_TEXT":   "#264f78",
    "HIGHLIGHT":  "#5a4a28",
    # Send / cancel button hover
    "SEND_HOVER": "#6ab5eb",
}

_MUTED_DARK: dict[str, str] = {
    # Status / method badges — muted and softer for outdoor visibility
    "GREEN":      "#26a641",
    "AMBER":      "#9d8501",
    "RED":        "#d1444f",
    "BLUE":       "#5fa3e8",
    "PURPLE":     "#8b7ec1",
    "MUTED":      "#787e87",
    "CYAN":       "#3ba7ac",
    "GRAY":       "#6e7681",
    "TEAL":       "#3ba7ac",
    # Surfaces — darker for outdoor use, softer grays
    "BG":         "#0a0e13",      # Slightly darker than normal dark for outdoor contrast
    "BG_ALT":     "#131820",      # Softer than normal dark
    "BORDER":     "#353c47",      # Muted borders
    "BORDER_FCS": "#6da6f0",      # Softer focus color
    # Text — excellent contrast for outdoor viewing (>8:1 ratios)
    "FG":         "#f0f2f5",      # Slightly warmer white
    "FG_MUTED":   "#c9cdd4",      # Muted secondary text (softer than regular dark)
    "FG_SUBTLE":  "#8e95a3",      # Subtle text (muted)
    # Selection / highlight — softer, less harsh
    "SELECTION":  "#1a2e42",      # Muted selection
    "SEL_TEXT":   "#3d5a7a",      # Softer selected text
    "HIGHLIGHT":  "#4a3d1f",      # Softer highlight
    # Send / cancel button hover — muted but still visible
    "SEND_HOVER": "#5d95dc",      # Softer hover
}

_OCEANIC: dict[str, str] = {
    # Status / method badges
    "GREEN":      "#40c463",
    "AMBER":      "#e3b341",
    "RED":        "#f85149",
    "BLUE":       "#58a6ff",
    "PURPLE":     "#bc8cff",
    "MUTED":      "#8b949e",
    "CYAN":       "#39c5cf",
    "GRAY":       "#6e7681",
    "TEAL":       "#56d4dd",
    # Surfaces — Deep oceanic blues
    "BG":         "#011627",      # Night owl dark blue
    "BG_ALT":     "#011f35",      # Slightly lighter blue
    "BORDER":     "#1d3b53",      # Muted blue border
    "BORDER_FCS": "#00d1ff",      # Bright cyan focus
    # Text
    "FG":         "#d6deeb",      # Soft white/blue
    "FG_MUTED":   "#92a1b5",
    "FG_SUBTLE":  "#5f7e97",
    # Selection / highlight
    "SELECTION":  "#1d3b53",
    "SEL_TEXT":   "#234d70",
    "HIGHLIGHT":  "#0b2942",
    # Send / cancel button hover
    "SEND_HOVER": "#70b1ff",
}

# Guard: all palettes must expose identical keys so QSS substitution never
# produces a KeyError at runtime when the theme switches.
assert _LIGHT.keys() == _DARK.keys(), (
    f"Palette key mismatch — _LIGHT vs _DARK: "
    f"only in _LIGHT: {_LIGHT.keys() - _DARK.keys()!r}, "
    f"only in _DARK: {_DARK.keys() - _LIGHT.keys()!r}"
)
assert _LIGHT.keys() == _MUTED_DARK.keys(), (
    f"Palette key mismatch — _LIGHT vs _MUTED_DARK: "
    f"only in _LIGHT: {_LIGHT.keys() - _MUTED_DARK.keys()!r}, "
    f"only in _MUTED_DARK: {_MUTED_DARK.keys() - _LIGHT.keys()!r}"
)
assert _LIGHT.keys() == _OCEANIC.keys(), (
    f"Palette key mismatch — _LIGHT vs _OCEANIC: "
    f"only in _LIGHT: {_LIGHT.keys() - _OCEANIC.keys()!r}, "
    f"only in _OCEANIC: {_OCEANIC.keys() - _LIGHT.keys()!r}"
)

# The active palette dict — replaced by apply_theme(); read by _ColorProxy at
# call time so all property accesses reflect the current theme immediately.
_active: dict[str, str] = dict(_LIGHT)


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


# ── Theme mode constants ─────────────────────────────────────────────────────

THEME_SYSTEM: str = "system"
THEME_LIGHT:  str = "light"
THEME_DARK:   str = "dark"
THEME_MUTED_DARK: str = "muted_dark"
THEME_OCEANIC: str = "oceanic"
THEME_MODES:  tuple[str, ...] = (THEME_SYSTEM, THEME_LIGHT, THEME_DARK, THEME_MUTED_DARK, THEME_OCEANIC)
THEME_LABELS: dict[str, str] = {
    "system": "System",
    "light": "Light",
    "dark": "Dark",
    "muted_dark": "Muted Dark",
    "oceanic": "Oceanic (Deep Blue)",
}



# ── Font / size / family defaults ─────────────────────────────────────────────

DEFAULT_FONT_SIZE: int = 9
DEFAULT_MONO_SIZE: int = 9
MIN_FONT_SIZE:     int = 6
MAX_FONT_SIZE:     int = 20

# Platform-aware font family defaults
_PLATFORM_MONO_FONTS = {
    "win32": ["Consolas", "Courier New", "Lucida Console", "DejaVu Sans Mono", "Menlo", "Monaco", "monospace"],
    "darwin": ["Menlo", "Monaco", "Consolas", "DejaVu Sans Mono", "Courier", "monospace"],
    "linux": ["DejaVu Sans Mono", "Liberation Mono", "Consolas", "Menlo", "Monaco", "monospace"],
}
_PLATFORM_UI_FONTS = {
    "win32": ["Segoe UI", "Arial", "Tahoma", "Verdana", "sans-serif"],
    "darwin": ["San Francisco", "Helvetica Neue", "Arial", "Verdana", "sans-serif"],
    "linux": ["DejaVu Sans", "Liberation Sans", "Arial", "Verdana", "sans-serif"],
}

def _get_platform_fonts(kind: str) -> list[str]:
    plat = sys.platform
    if plat.startswith("win"): plat = "win32"
    elif plat == "darwin": plat = "darwin"
    else: plat = "linux"
    return (_PLATFORM_MONO_FONTS if kind == "mono" else _PLATFORM_UI_FONTS)[plat]

# User font family preference keys
_MONO_FONT_KEY = "appearance/mono_font_family"
_UI_FONT_KEY = "appearance/ui_font_family"

# Secondary-label size: base_pt minus this reduction, but never below the floor.
_SM_FONT_REDUCTION: int = 2
_SM_MIN_PT:         int = 7


# ── Settings persistence ─────────────────────────────────────────────────────

def _settings() -> QSettings:
    return QSettings("Equinox", "Equinox")


# ── Private helpers ───────────────────────────────────────────────────────────

def _clamp_font_size(size: int) -> int:
    """Return *size* clamped to ``[MIN_FONT_SIZE, MAX_FONT_SIZE]``."""
    return max(MIN_FONT_SIZE, min(MAX_FONT_SIZE, size))


# ── Settings accessors ────────────────────────────────────────────────────────

def get_font_size() -> int:
    """Return the user-chosen base font size (pt)."""
    s = _settings()
    val = s.value("appearance/font_size", DEFAULT_FONT_SIZE, type=int)
    return _clamp_font_size(val)


def set_font_size(size: int) -> None:
    """Persist the base font size and immediately re-apply the theme."""
    _settings().setValue("appearance/font_size", _clamp_font_size(size))
    apply_theme()


def get_theme_mode() -> str:
    """Return the persisted theme mode (``system``, ``light``, ``dark``, or ``muted_dark``)."""
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
    """Return a monospaced QFont at the user-chosen (or overridden) size and family."""
    sz = size_override if size_override is not None else get_font_size()
    fam = _settings().value(_MONO_FONT_KEY, None, type=str)
    if fam:
        f = QFont(fam, sz)
    else:
        # Try platform-appropriate fonts in order
        for font_name in _get_platform_fonts("mono"):
            f = QFont(font_name, sz)
            if f.exactMatch():
                break
        else:
            f = QFont("monospace", sz)
    f.setStyleHint(QFont.StyleHint.Monospace)
    return f


def get_ui_font(size_override: int | None = None) -> QFont:
    """Return the default UI QFont at the user-chosen (or overridden) size and family."""
    sz = size_override if size_override is not None else get_font_size()
    fam = _settings().value(_UI_FONT_KEY, None, type=str)
    if fam:
        f = QFont(fam, sz)
    else:
        for font_name in _get_platform_fonts("ui"):
            f = QFont(font_name, sz)
            if f.exactMatch():
                break
        else:
            f = QFont("sans-serif", sz)
    f.setStyleHint(QFont.StyleHint.SansSerif)
    return f
# ── Public API ───────────────────────────────────────────────────────────────

# Palette validation: ensure all palettes have the same keys at runtime
def validate_palettes() -> None:
    palettes = {
        "LIGHT": _LIGHT,
        "DARK": _DARK,
        "MUTED_DARK": _MUTED_DARK,
        "OCEANIC": _OCEANIC,
    }
    keys = set(_LIGHT.keys())
    for name, pal in palettes.items():
        missing = keys - set(pal.keys())
        extra = set(pal.keys()) - keys
        if missing or extra:
            import warnings
            warnings.warn(f"Palette {name} mismatch: missing={missing}, extra={extra}")

# Call at module import and after theme changes
validate_palettes()


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
    """Return True when the active palette should be dark (including muted dark)."""
    mode = get_theme_mode()
    if mode in (THEME_DARK, THEME_MUTED_DARK, THEME_OCEANIC):
        return True
    if mode == THEME_LIGHT:
        return False
    return _system_is_dark()


def _resolve_palette() -> dict[str, str]:
    """Return the appropriate palette dict for the current theme settings."""
    mode = get_theme_mode()

    if mode == THEME_MUTED_DARK:
        return _MUTED_DARK
    elif mode == THEME_DARK:
        return _DARK
    elif mode == THEME_OCEANIC:
        return _OCEANIC
    elif mode == THEME_LIGHT:
        return _LIGHT
    else:
        # System mode: use system detection
        if _system_is_dark():
            return _DARK
        return _LIGHT


def is_dark() -> bool:
    """Return True if the current resolved theme is dark."""
    return _resolve_dark()


# ── Master stylesheet ───────────────────────────────────────────────────────

def _build_stylesheet(base_pt: int) -> str:
    """Generate the application-wide QSS string."""
    C = _active
    sm = max(base_pt - _SM_FONT_REDUCTION, _SM_MIN_PT)

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
    QPushButton, QToolButton {{
        padding: 4px 8px;
        border: 1px solid {C["BORDER"]};
        border-radius: 4px;
        background: {C["BG"]};
        color: {C["FG"]};
        min-height: 1.5em;
    }}
    QToolButton {{
        padding-right: 18px;
    }}
    QPushButton:hover, QToolButton:hover {{
        background: {C["BG_ALT"]};
        border-color: {C["FG_MUTED"]};
    }}
    QPushButton:pressed, QToolButton:pressed {{
        background: {C["BORDER"]};
    }}
    QPushButton:disabled, QToolButton:disabled {{
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
    
    QWidget#field-valid {{
        border-color: {C["GREEN"]} !important;
    }}
    
    QWidget#field-error {{
        border-color: {C["RED"]} !important;
    }}

    /* ── Tabs ───────────────────────────────────────────── */
    QTabWidget::pane {{
        border: 1px solid {C["BORDER"]};
        background: {C["BG"]};
        border-radius: 4px;
    }}
    QTabBar::tab {{
        padding: 8px 16px;
        border: 1px solid transparent;
        margin-right: 4px;
        color: {C["FG_MUTED"]};
        font-size: {int(sm*1.2)}pt;
        background: transparent;
        border-radius: 4px;
    }}
    QTabBar::tab:selected {{
        color: {C["BLUE"]};
        background: {C["BG"]};
        border: 1px solid {C["BORDER"]};
    }}
    QTabBar[tabPosition="0"]::tab:selected {{
        border-bottom: 2px solid {C["BLUE"]};
    }}
    QTabBar[tabPosition="1"]::tab:selected {{
        border-top: 2px solid {C["BLUE"]};
    }}
    QTabBar::tab:hover:!selected {{
        color: {C["FG"]};
        background: {C["BG_ALT"]};
    }}

    /* ── Main Sidebar (Prototyping) ───────────────────────── */
    QWidget#sidebar {{
        background: {C["BG_ALT"]};
        border-right: 1px solid {C["BORDER"]};
        min-width: 50px;
        max-width: 50px;
    }}
    QToolButton#sidebarBtn {{
        border: none;
        border-radius: 0px;
        background: transparent;
        padding: 10px;
        min-height: 40px;
    }}
    QToolButton#sidebarBtn:hover {{
        background: {C["SELECTION"]};
    }}
    QToolButton#sidebarBtn:checked {{
        background: {C["BG"]};
        border-left: 3px solid {C["BLUE"]};
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
        width: 1px;
        margin: 0px 2px;
    }}
    QSplitter::handle:vertical {{
        height: 1px;
        margin: 2px 0px;
    }}
    QSplitter::handle:hover {{
        background: {C["BLUE"]};
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

_ss_cache: dict[tuple[str, int], str] = {}


def _palette_cache_key(palette: dict[str, str]) -> str:
    """Return a stable cache key for a resolved palette object."""
    if palette is _LIGHT:
        return THEME_LIGHT
    if palette is _DARK:
        return THEME_DARK
    if palette is _MUTED_DARK:
        return THEME_MUTED_DARK
    if palette is _OCEANIC:
        return THEME_OCEANIC
    # Fallback keeps cache safe even if a future custom palette is injected.
    return "custom"


def apply_theme(app: QApplication | None = None) -> None:
    """(Re-)apply the global stylesheet to the running QApplication.

    Resolves the effective palette (light/dark/muted_dark/system), updates the
    ``Colors`` proxy, sets the application font, and installs the
    generated stylesheet.

    The stylesheet string is cached by ``(resolved_palette, base_pt)`` so repeated
    calls with unchanged settings avoid the cost of rebuilding and re-parsing
    a ~450-line QSS document.
    """
    global _active

    if app is None:
        app = QApplication.instance()
    if app is None:
        return

    # Resolve effective palette
    palette = _resolve_palette()
    _active = palette

    # Validate palettes after theme change
    validate_palettes()

    base_pt = get_font_size()
    app.setFont(get_ui_font(base_pt))

    cache_key = (_palette_cache_key(palette), base_pt)
    if cache_key not in _ss_cache:
        _ss_cache[cache_key] = _build_stylesheet(base_pt)
    app.setStyleSheet(_ss_cache[cache_key])
