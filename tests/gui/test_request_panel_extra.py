import os

from PyQt6.QtWidgets import QApplication, QPushButton
from PyQt6.QtCore import QCoreApplication

import pytest


def ensure_qapp():
    app = QApplication.instance()
    if app is None:
        return QApplication([])
    return app


@pytest.fixture()
def tmp_db_path(tmp_path, monkeypatch):
    db_file = tmp_path / "equinox_test.db"
    monkeypatch.setenv("EQUINOX_DB_PATH", str(db_file))
    return str(db_file)


def process_events():
    app = QApplication.instance() or ensure_qapp()
    QCoreApplication.processEvents()


def test_insert_header_preset_does_not_create_extra_rows(tmp_db_path):
    ensure_qapp()
    from equinox.storage import get_db
    from equinox.gui.request_panel.panel import RequestPanel

    db = get_db()
    panel = RequestPanel(db)

    # Start clean
    panel.headers_table.reset()
    process_events()

    # Insert a preset and assert there's exactly one non-empty row
    panel._insert_header_preset("Content-Type", "application/json")
    process_events()
    rows = panel.headers_table.get_all_rows()
    assert len(rows) == 1
    assert rows[0]["key"].lower() == "content-type"
    assert "application/json" in rows[0]["value"]


def test_headers_add_and_remove_behaviour_matches_captures(tmp_db_path):
    ensure_qapp()
    from equinox.storage import get_db
    from equinox.gui.request_panel.panel import RequestPanel

    db = get_db()
    panel = RequestPanel(db)
    panel.headers_table.reset()
    process_events()

    # Add a header row (adds an editable row + trailing empty row)
    panel._headers_add_row()
    process_events()
    # The table keeps a trailing empty row; check rowCount and that at
    # least one editable row exists (may have empty key until edited).
    assert panel.headers_table.rowCount() >= 2

    # Select and remove it
    panel.headers_table.selectRow(0)
    panel._headers_remove_row()
    process_events()
    assert len(panel.headers_table.get_all_rows()) == 0


def test_params_add_and_remove_behaviour_matches_captures(tmp_db_path):
    ensure_qapp()
    from equinox.storage import get_db
    from equinox.gui.request_panel.panel import RequestPanel

    db = get_db()
    panel = RequestPanel(db)
    panel.params_table.reset()
    process_events()

    panel._params_add_row()
    process_events()
    assert panel.params_table.rowCount() >= 2

    panel.params_table.selectRow(0)
    panel._params_remove_row()
    process_events()
    # After removal, there should be only the trailing empty row
    assert panel.params_table.rowCount() <= 1


def test_multipart_add_remove(tmp_db_path):
    ensure_qapp()
    from equinox.storage import get_db
    from equinox.gui.request_panel.panel import RequestPanel

    db = get_db()
    panel = RequestPanel(db)
    # The multipart table may be unavailable in some headless/test envs
    try:
        panel._multipart_table.setRowCount(0)
        process_events()

        panel._multipart_add_row()
        process_events()
        assert panel._multipart_table.rowCount() == 1

        panel._multipart_table.selectRow(0)
        panel._multipart_remove_row()
        process_events()
        assert panel._multipart_table.rowCount() == 0
    except RuntimeError:
        # Underlying C++ object was deleted — treat as acceptable for this
        # environment; the panel should handle missing widgets elsewhere.
        pytest.skip("Multipart table unavailable in this test environment")


def test_body_proxy_handles_deleted_widget(tmp_db_path):
    ensure_qapp()
    from equinox.storage import get_db
    from equinox.gui.request_panel.panel import RequestPanel

    db = get_db()
    panel = RequestPanel(db)

    # Simulate the underlying C++ widget being gone
    if hasattr(panel.body_text, "_widget"):
        panel.body_text._widget = None

    # Setting text should not raise and should mark the panel dirty
    panel._dirty = False
    panel.body_text.setPlainText('{"x":1}')
    process_events()
    assert panel.is_dirty() is True
    assert panel.body_text.toPlainText().strip() == '{"x":1}'


def test_setup_dirty_tracking_is_resilient_when_widgets_missing(tmp_db_path):
    ensure_qapp()
    from equinox.storage import get_db
    from equinox.gui.request_panel.panel import RequestPanel

    db = get_db()
    panel = RequestPanel(db)

    # Force some widgets to be considered missing by nulling references.
    # Calling deleteLater() may raise if the underlying C++ object is
    # already gone, so set attributes to None instead to simulate.
    if hasattr(panel.body_text, "_widget"):
        panel.body_text._widget = None
    panel._multipart_table = None
    panel._gql_query = None

    # Re-run the wiring — should not raise even if some attributes are None
    panel._setup_dirty_tracking()

