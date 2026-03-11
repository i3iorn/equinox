import os

# Force Qt into offscreen/platformless mode for tests so dialogs don't require
# a display. Must be set before importing PyQt widgets.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

# Ensure a single QApplication exists for the duration of the test session.
_APP = QApplication.instance() or QApplication([])

