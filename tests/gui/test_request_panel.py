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


def test_request_panel_shows_draft_state_feedback(tmp_db_path):
    """Dirty/saved state should be visible without opening any dialogs."""
    ensure_qapp()
    from equinox.storage import get_db
    from equinox.core.request import Request
    from equinox.gui.request_panel.panel import RequestPanel

    db = get_db()
    panel = RequestPanel(db)

    assert getattr(panel, "save_button").text() == "Save"
    assert getattr(panel, "_editor_state_label").text() == "Scratch request"

    panel.body_text.setPlainText('{"draft": true}')
    process_events()

    assert panel.is_dirty() is True
    assert getattr(panel, "save_button").text() == "Save Changes"
    assert getattr(panel, "_editor_state_label").text() == "Unsaved changes"

    saved = Request(method="GET", url="https://example.com")
    saved.id = 42
    panel.current_request = saved
    panel._clear_dirty()

    assert getattr(panel, "save_button").text() == "Save"
    assert getattr(panel, "_editor_state_label").text() == "Saved to collection"


def test_request_panel_restores_last_active_tab(tmp_db_path):
    """The request editor should reopen on the last tab the user selected."""
    ensure_qapp()
    from equinox.storage import get_db
    from equinox.gui.request_panel.panel import RequestPanel
    from equinox.gui.ui_common import get_gui_settings

    settings = get_gui_settings()
    settings.remove("request/active_tab")
    settings.sync()

    db = get_db()
    first = RequestPanel(db)
    target_idx = next(
        idx for idx in range(first.tabs.count()) if first.tabs.tabText(idx).startswith("Notes")
    )
    first.tabs.setCurrentIndex(target_idx)
    process_events()

    second = RequestPanel(db)
    assert second.tabs.tabText(second.tabs.currentIndex()).startswith("Notes")

    settings.remove("request/active_tab")
    settings.sync()


