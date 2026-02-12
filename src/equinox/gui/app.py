"""Main GUI application"""

import sys
from pathlib import Path
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt

from equinox.gui.window import MainWindow
from equinox.storage import Database


def main():
    """Launch GUI application"""
    # Set up high DPI scaling
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    app.setApplicationName("Equinox")
    app.setOrganizationName("Equinox")

    # Initialize database
    db_path = Path.home() / ".equinox" / "equinox.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = Database(str(db_path))

    # Create main window
    window = MainWindow(db)
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
