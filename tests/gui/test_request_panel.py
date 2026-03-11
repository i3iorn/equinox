import os
import tempfile

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QCoreApplication

import pytest


def ensure_qapp():
    """Ensure a QApplication exists for tests (safe to call multiple times)."""
    app = QApplication.instance()
    if app is None:
        _app = QApplication([])
        return _app
    return app


@pytest.fixture()
def tmp_db_path(tmp_path, monkeypatch):
    # Use a temp file for the DB so tests don't touch the user's real DB
    db_file = tmp_path / "equinox_test.db"
    monkeypatch.setenv("EQUINOX_DB_PATH", str(db_file))
    return str(db_file)


def process_events():
    # Process pending Qt events to ensure signals are delivered in tests
    app = QApplication.instance() or ensure_qapp()
    QCoreApplication.processEvents()


def test_request_panel_initializes_without_crash(tmp_db_path):
    """Constructing RequestPanel should not raise and widgets should exist."""
    # Ensure QApplication
    ensure_qapp()

    # Import here so the environment variable is active before DB is created
    from equinox.storage import get_db
    from equinox.gui.request_panel.panel import RequestPanel

    db = get_db()
    panel = RequestPanel(db)

    # Basic attributes created
    assert hasattr(panel, "body_text") and panel.body_text is not None
    assert hasattr(panel, "_session_vars_label") and panel._session_vars_label is not None

    # Clearing session vars should update the label (emit/slot dispatched)
    panel._session_vars_label.setText("Session vars: 999")
    panel.clear_session_vars()
    process_events()
    assert panel._session_vars_label.text().startswith("Session vars:")
    # Should be zero after clear
    assert panel._session_vars_label.text().endswith("0")


def test_body_edit_marks_dirty(tmp_db_path):
    """Editing the body editor should mark the panel dirty via textChanged signal."""
    ensure_qapp()
    from equinox.storage import get_db
    from equinox.gui.request_panel.panel import RequestPanel

    db = get_db()
    panel = RequestPanel(db)
    # Ensure initial clean state
    panel._dirty = False

    # Change the body editor text — should trigger textChanged and mark dirty
    panel.body_text.setPlainText("{\n  \"a\": 1\n}")
    process_events()
    assert panel.is_dirty() is True

