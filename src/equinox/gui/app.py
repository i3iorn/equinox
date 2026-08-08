"""Main GUI application.

Responsibilities:
- Bootstrap sequence: logging → Qt → theme → database → window
- Exception handling and error reporting
- Splash screen lifecycle during startup
- Graceful shutdown logging
"""

from __future__ import annotations

import logging
import sys
import traceback
import types
from pathlib import Path
from typing import Any, NoReturn

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPixmap
from PyQt6.QtWidgets import QApplication, QSplashScreen

from equinox.core.log_setup import configure_logging
from equinox.gui.dialogs.master_password_dialog import prompt_master_password
from equinox.gui.theme import apply_theme
from equinox.gui.widgets import CopyableMessageBox
from equinox.gui.window import MainWindow
from equinox.security.secrets_password import set_master_password_prompt
from equinox.storage import get_db
from equinox.versioning import get_app_version

__all__ = ["main"]

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Splash Screen Configuration
# ──────────────────────────────────────────────────────────────────────────────

_SPLASH_WIDTH: int = 420
_SPLASH_HEIGHT: int = 100
_SPLASH_BG_COLOR: str = "#1e1e2e"
_SPLASH_TEXT_COLOR: str = "#cdd6f4"
_SPLASH_ALIGN = Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter
_SPLASH_INITIAL_MSG: str = "Starting Equinox…"

# ──────────────────────────────────────────────────────────────────────────────
# Startup Messages
# ──────────────────────────────────────────────────────────────────────────────

_MSG_INITIALIZING_DB: str = "Initialising database…"
_MSG_LOADING_INTERFACE: str = "Loading interface…"
_ERR_LOGGING_SETUP: str = "FATAL: Could not configure logging: %s"
_ERR_DB_INIT: str = "Equinox could not open its database:\n\n%s"
_ERR_STARTUP: str = "Equinox could not start:\n\n%s"


def _get_app_version() -> str:
    """Backward-compatible wrapper for legacy imports."""
    return get_app_version()


# ──────────────────────────────────────────────────────────────────────────────
# Exception Handling
# ──────────────────────────────────────────────────────────────────────────────


def _install_qt_exception_hook() -> None:
    """Install a custom exception hook for the Qt main thread.

    Catches unhandled exceptions (except SystemExit and KeyboardInterrupt),
    logs them, and shows a user-friendly error dialog with copyable traceback.
    """
    sys.excepthook = _qt_exception_hook


def _qt_exception_hook(
    exc_type: type[BaseException],
    exc_value: BaseException,
    exc_tb: types.TracebackType | None,
) -> None:
    """Catch unhandled exceptions on the Qt main thread, log them, and show a dialog.

    ``SystemExit`` and ``KeyboardInterrupt`` are forwarded to the default hook
    so normal process termination is never intercepted by the error dialog.

    Args:
        exc_type: Exception type
        exc_value: Exception instance
        exc_tb: Traceback object
    """
    # Allow normal process termination
    if issubclass(exc_type, (SystemExit, KeyboardInterrupt)):
        sys.__excepthook__(exc_type, exc_value, exc_tb)
        return

    # Format and log the full traceback
    msg = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    logger.critical("Unhandled exception on Qt thread:\n%s", msg)

    # Show user-friendly error dialog with copyable details
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
        # Dialog itself failed; exception is already logged above
        pass


# ──────────────────────────────────────────────────────────────────────────────
# Splash Screen Helper
# ──────────────────────────────────────────────────────────────────────────────


class _SplashScreen:
    """Manages the splash screen lifecycle during startup.

    Provides a clean API for updating messages and ensures the UI stays
    responsive via event processing after each update.

    Attributes:
        screen: The underlying QSplashScreen instance
    """

    def __init__(self) -> None:
        """Create and initialize the splash screen."""
        pixmap = QPixmap(_SPLASH_WIDTH, _SPLASH_HEIGHT)
        pixmap.fill(QColor(_SPLASH_BG_COLOR))
        self.screen = QSplashScreen(pixmap, Qt.WindowType.WindowStaysOnTopHint)

    def show(self) -> None:
        """Display the splash screen and pump events for responsiveness."""
        self.screen.showMessage(_SPLASH_INITIAL_MSG, _SPLASH_ALIGN, QColor(_SPLASH_TEXT_COLOR))
        self.screen.show()
        QApplication.processEvents()

    def update(self, text: str) -> None:
        """Update the splash message and pump events to keep UI responsive.

        Args:
            text: Message text to display
        """
        self.screen.showMessage(text, _SPLASH_ALIGN, QColor(_SPLASH_TEXT_COLOR))
        QApplication.processEvents()

    def close(self) -> None:
        """Close the splash screen."""
        self.screen.close()

    def finish(self, window: MainWindow) -> None:
        """Dismiss the splash when the main window is ready.

        Args:
            window: The main application window
        """
        self.screen.finish(window)


# ──────────────────────────────────────────────────────────────────────────────
# Startup Steps
# ──────────────────────────────────────────────────────────────────────────────


def _init_logging() -> Path:
    """Initialize application logging.

    Must be called before anything else so exceptions can be properly logged.

    Returns:
        Path to the log file

    Raises:
        SystemExit: If logging initialization fails
    """
    try:
        return configure_logging(console_level=logging.WARNING)
    except Exception as exc:
        print(_ERR_LOGGING_SETUP % exc, file=sys.stderr)
        sys.exit(1)


def _init_qt_application(app_version: str) -> QApplication:
    """Initialize the Qt application with UI settings.

    Args:
        app_version: Application version string

    Returns:
        Configured QApplication instance
    """
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough,
    )
    app = QApplication(sys.argv)
    app.setApplicationName("Equinox")
    app.setOrganizationName("Equinox")
    app.setApplicationVersion(app_version)
    return app


def _init_database(splash: _SplashScreen) -> Any:
    """Initialize the database (may run migrations on first launch).

    Args:
        splash: Splash screen for status updates

    Returns:
        Database instance

    Raises:
        SystemExit: If database initialization fails
    """
    splash.update(_MSG_INITIALIZING_DB)
    try:
        return get_db()
    except Exception as exc:
        _show_fatal_error(splash, "Database Error", _ERR_DB_INIT % exc)


def _configure_master_password_gui_prompt(app: QApplication) -> None:
    """Route master-password prompts through a Qt dialog for GUI sessions."""

    def _prompt() -> str | None:
        parent = app.activeWindow() if app.activeWindow() is not None else None
        return prompt_master_password(parent)

    set_master_password_prompt(_prompt)


def _init_main_window(splash: _SplashScreen, db: object) -> MainWindow:
    """Initialize the main application window.

    Args:
        splash: Splash screen for status updates
        db: Database instance

    Returns:
        Configured MainWindow instance

    Raises:
        SystemExit: If window initialization fails
    """
    splash.update(_MSG_LOADING_INTERFACE)
    try:
        return MainWindow(db)
    except Exception as exc:
        _show_fatal_error(splash, "Startup Error", _ERR_STARTUP % exc)


def _show_fatal_error(splash: _SplashScreen, title: str, message: str) -> NoReturn:
    """Display a fatal error dialog and terminate the application.

    Must be called from within an except block so that traceback.format_exc()
    captures the active exception context. The full traceback is offered via
    the dialog's Copy button without being shown in the visible message text.

    Args:
        splash: Splash screen to close
        title: Error dialog title
        message: Error message to display

    Raises:
        SystemExit: Always (app terminates with exit code 1)
    """
    logger.critical("Fatal startup error — %s", title, exc_info=True)
    splash.close()
    CopyableMessageBox.critical(
        None,
        title,
        f"{message}\n\nCheck the log file for details.",
        copy_text=traceback.format_exc(),
    )
    sys.exit(1)


# ──────────────────────────────────────────────────────────────────────────────
# Main Entry Point
# ──────────────────────────────────────────────────────────────────────────────


def main() -> NoReturn:
    """Launch the Equinox GUI application.

    Bootstrap sequence:
    1. Initialize logging (must be first, can only report errors to stderr)
    2. Install exception hook for Qt main thread
    3. Create Qt application with settings
    4. Apply theme
    5. Show splash screen
    6. Initialize database (may run migrations)
    7. Initialize main window
    8. Connect shutdown handlers
    9. Run event loop

    Raises:
        SystemExit: Always (with status 0 on normal exit, 1 on error)
    """
    # Step 1: Initialize logging (first, before exception hook)
    log_file = _init_logging()
    app_version = _get_app_version()
    logger.info("Equinox GUI starting — version %s", app_version)

    # Step 2: Install custom exception hook for runtime errors
    _install_qt_exception_hook()

    # Step 3: Initialize Qt application
    app = _init_qt_application(app_version)

    # Route master-password prompts through GUI dialog (no terminal getpass).
    _configure_master_password_gui_prompt(app)

    # Step 4: Apply theme
    apply_theme(app)

    # Step 5: Show splash screen (keeps UI responsive during startup)
    splash = _SplashScreen()
    splash.show()

    # Step 6: Initialize database (may run schema migrations)
    db = _init_database(splash)

    # Step 7: Initialize main window
    window = _init_main_window(splash, db)
    window.statusBar().showMessage(f"Ready  |  Log: {log_file}", 6000)

    # Step 8: Connect shutdown logging
    app.aboutToQuit.connect(lambda: logger.info("Equinox GUI shutting down"))

    # Step 9: Show window and dismiss splash
    window.show()
    splash.finish(window)

    logger.info("Equinox GUI ready")
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
