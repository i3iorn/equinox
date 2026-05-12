import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"

if str(_SRC) not in sys.path:
	sys.path.insert(0, str(_SRC))

# Force Qt into offscreen/platformless mode for tests so dialogs don't require
# a display. Must be set before importing PyQt widgets.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Keep auth-cipher initialization non-interactive in tests.
os.environ.setdefault("EQUINOX_MASTER_PASSWORD", "test-master-password")

from PyQt6.QtWidgets import QApplication

# Ensure a single QApplication exists for the duration of the test session.
_APP = QApplication.instance() or QApplication([])

