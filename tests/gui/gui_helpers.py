"""Shared helpers for GUI tests.

`APP` and `process` were each re-declared in eight GUI test modules. The
QApplication in particular was re-derived every time even though the root
tests/conftest.py already constructs one before any test module is imported,
so every copy was just re-fetching the same instance.
"""

from __future__ import annotations

from PyQt6.QtCore import QCoreApplication
from PyQt6.QtWidgets import QApplication

__all__ = ["APP", "process"]

#: The session-wide QApplication. Created by tests/conftest.py at import time;
#: this only retrieves it (falling back to construction for direct runs).
APP = QApplication.instance() or QApplication([])


def process() -> None:
    """Let Qt deliver queued events (signals, timers, deferred layout)."""
    QCoreApplication.processEvents()
