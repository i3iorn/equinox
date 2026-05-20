"""System appearance detection helpers."""

from __future__ import annotations

import sys

from PyQt6.QtGui import QPalette
from PyQt6.QtWidgets import QApplication


def system_is_dark(app: QApplication | None = None) -> bool:
    """Best-effort detection of whether the OS/application palette is dark."""
    if sys.platform == "win32":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
            ) as key:
                value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return value == 0
        except Exception:
            pass

    qt_app = app if app is not None else QApplication.instance()
    if isinstance(qt_app, QApplication):
        palette = qt_app.palette()
        window_color = palette.color(QPalette.ColorRole.Window)
        return window_color.lightnessF() < 0.5

    return False
