"""Main GUI application."""
from __future__ import annotations

import logging
import sys
import traceback
import types
from importlib.metadata import PackageNotFoundError, version
from typing import NoReturn

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPixmap
from PyQt6.QtWidgets import QApplication, QSplashScreen

from equinox.core.log_setup import configure_logging
from equinox.gui.theme import apply_theme
from equinox.gui.widgets import CopyableMessageBox
from equinox.gui.window import MainWindow
from equinox.storage import get_db

__all__ = ["main"]

logger = logging.getLogger(__name__)

# ── Helpers ───────────────────────────────────────────────────────────────────


def _app_version() -> str:
    """Return the installed package version, or ``'dev'`` if not installed."""
    try:
        return version("equinox")
    except PackageNotFoundError:
        return "dev"


def _qt_exception_hook(
    exc_type: type[BaseException],
    exc_value: BaseException,
    exc_tb: types.TracebackType | None,
) -> None:
    """Catch unhandled exceptions on the Qt main thread, log them, and show a dialog.

    ``SystemExit`` and ``KeyboardInterrupt`` are forwarded to the default hook
    so normal process termination is never intercepted by the error dialog.
    """
    if issubclass(exc_type, (SystemExit, KeyboardInterrupt)):
        sys.__excepthook__(exc_type, exc_value, exc_tb)
        return

    msg = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    logger.critical("Unhandled exception on Qt thread:\n%s", msg)

    try:
        CopyableMessageBox.critical(
            None,
            "Unexpected Error",
            f"{exc_type.__name__}: {exc_value}\n\n"
            "Details have been written to the log file.\n"
            "Please restart the application if it appears unstable.",
            copy_text=msg,
        )
    except Exception:
        pass  # Dialog itself failed; the error is already in the log.


def _make_splash() -> QSplashScreen:
    """Create a minimal splash screen shown during startup initialisation."""
    pixmap = QPixmap(420, 100)
    pixmap.fill(QColor("#1e1e2e"))
    splash = QSplashScreen(pixmap, Qt.WindowType.WindowStaysOnTopHint)
    splash.showMessage(
        "Starting Equinox…",
        Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter,
        QColor("#cdd6f4"),
    )
    return splash


def _splash_msg(splash: QSplashScreen, app: QApplication, text: str) -> None:
    """Update *splash* message and pump pending events so the UI stays responsive."""
    splash.showMessage(
        text,
        Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter,
        QColor("#cdd6f4"),
    )
    app.processEvents()


# ── Entry point ───────────────────────────────────────────────────────────────


def main() -> NoReturn:
    """Launch the Equinox GUI application."""
    # ── Logging must be configured before anything else ──────────────────
    log_file = configure_logging(console_level=logging.WARNING)
    logger.info("Equinox GUI starting — version %s", _app_version())

    # ── Install exception hook ───────────────────────────────────────────
    sys.excepthook = _qt_exception_hook

    # ── Qt application ───────────────────────────────────────────────────
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setApplicationName("Equinox")
    app.setOrganizationName("Equinox")
    app.setApplicationVersion(_app_version())

    # ── Apply theme ──────────────────────────────────────────────────────
    apply_theme(app)

    # ── Splash screen (keeps the UI responsive during initialisation) ────
    splash = _make_splash()
    splash.show()
    app.processEvents()

    # ── Database (may run schema migrations on first launch) ─────────────
    _splash_msg(splash, app, "Initialising database…")
    try:
        db = get_db()
    except Exception as exc:
        logger.critical("Failed to initialise database: %s", exc, exc_info=True)
        splash.close()
        CopyableMessageBox.critical(
            None,
            "Database Error",
            f"Equinox could not open its database:\n\n{exc}\n\n"
            "Check the log file for details.",
            copy_text=traceback.format_exc(),
        )
        sys.exit(1)

    # ── Main window ──────────────────────────────────────────────────────
    _splash_msg(splash, app, "Loading interface…")
    try:
        window = MainWindow(db)
    except Exception as exc:
        logger.critical("Failed to create main window: %s", exc, exc_info=True)
        splash.close()
        CopyableMessageBox.critical(
            None,
            "Startup Error",
            f"Equinox could not start:\n\n{exc}\n\n"
            "Check the log file for details.",
            copy_text=traceback.format_exc(),
        )
        sys.exit(1)

    window.statusBar().showMessage(f"Ready  |  Log: {log_file}", 6000)

    # ── Connect clean-shutdown logging ───────────────────────────────────
    app.aboutToQuit.connect(lambda: logger.info("Equinox GUI shutting down"))

    # ── Show window, then dismiss splash once it is visible ──────────────
    window.show()
    splash.finish(window)

    logger.info("Equinox GUI ready")
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
