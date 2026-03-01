"""Main GUI application"""

import sys
import logging
import traceback
from PyQt6.QtWidgets import QApplication, QMessageBox
from PyQt6.QtCore import Qt

from equinox.gui.window import MainWindow
from equinox.storage import get_db
from equinox.core.log_setup import configure_logging
from equinox.gui.theme import apply_theme

logger = logging.getLogger(__name__)


def _qt_exception_hook(exc_type, exc_value, exc_tb):
    """Catch unhandled exceptions on the Qt main thread, log them and show a dialog."""
    msg = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    logger.critical("Unhandled exception on Qt thread:\n%s", msg)
    try:
        QMessageBox.critical(
            None,
            "Unexpected Error",
            f"{exc_type.__name__}: {exc_value}\n\n"
            f"Details have been written to the log file.\n"
            f"Please restart the application if it appears unstable.",
        )
    except Exception:
        pass
    # Don't call sys.__excepthook__ — that would crash the process


def main():
    """Launch GUI application"""
    # ── Logging must be configured before anything else ───────────────
    log_file = configure_logging(console_level=logging.WARNING)
    logger.info("Equinox GUI starting up")

    # ── Install exception hook ────────────────────────────────────────
    sys.excepthook = _qt_exception_hook

    # ── Qt application ────────────────────────────────────────────────
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setApplicationName("Equinox")
    app.setOrganizationName("Equinox")

    # ── Apply theme ───────────────────────────────────────────────────
    apply_theme(app)

    # ── Database ──────────────────────────────────────────────────────
    db = get_db()


    # ── Main window ───────────────────────────────────────────────────
    window = MainWindow(db)
    window.statusBar().showMessage(f"Ready  |  Log: {log_file}", 6000)
    window.show()

    logger.info("Equinox GUI ready")
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
