import pytest
from PyQt6.QtCore import QCoreApplication
from PyQt6.QtWidgets import QApplication


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
    from equinox.gui.request_panel.panel import RequestPanel
    from equinox.storage import get_db

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
    from equinox.gui.request_panel.panel import RequestPanel
    from equinox.storage import get_db

    db = get_db()
    panel = RequestPanel(db)
    # Ensure initial clean state
    panel._dirty = False

    # Change the body editor text — should trigger textChanged and mark dirty
    panel.body_text.setPlainText('{\n  "a": 1\n}')
    process_events()
    assert panel.is_dirty() is True


def test_request_panel_shows_draft_state_feedback(tmp_db_path):
    """Dirty/saved state should be visible without opening any dialogs."""
    ensure_qapp()
    from equinox.core.request import Request
    from equinox.gui.request_panel.panel import RequestPanel
    from equinox.storage import get_db

    db = get_db()
    panel = RequestPanel(db)

    assert panel.save_button.text() == "Save"
    assert panel._editor_state_label.text() == "Scratch request"

    panel.body_text.setPlainText('{"draft": true}')
    process_events()

    assert panel.is_dirty() is True
    assert panel.save_button.text() == "Save Changes"
    assert panel._editor_state_label.text() == "Unsaved changes"

    saved = Request(method="GET", url="https://example.com")
    saved.id = 42
    panel.current_request = saved
    panel._clear_dirty()

    assert panel.save_button.text() == "Save"
    assert panel._editor_state_label.text() == "Saved to collection"


def test_request_panel_restores_last_active_tab(tmp_db_path):
    """The request editor should reopen on the last tab the user selected."""
    ensure_qapp()
    from equinox.gui.request_panel.panel import RequestPanel
    from equinox.gui.ui_common import get_gui_settings
    from equinox.storage import get_db

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


def test_cancel_request_does_not_block_ui_thread(tmp_db_path):
    """Cancel must call worker.cancel() and reset UI state immediately,
    without blocking on worker.wait() (regression test for a Cancel button
    that could freeze the whole GUI for up to 2 seconds)."""
    from unittest.mock import MagicMock

    ensure_qapp()
    from equinox.gui.request_panel.panel import RequestPanel
    from equinox.storage import get_db

    db = get_db()
    panel = RequestPanel(db)

    worker = MagicMock()
    panel._worker = worker
    panel._set_sending_state(True)

    panel._cancel_request()

    worker.cancel.assert_called_once()
    worker.wait.assert_not_called()
    assert panel._worker is None
    assert panel.send_button.isEnabled() is True
    assert panel.cancel_button.isVisible() is False


def test_send_button_disabled_when_headers_invalid(tmp_db_path):
    """Invalid headers must actually disable Send, not just show an error
    style — regression test for a dead-code branch that let Send stay
    enabled whenever body validation happened to still be True."""
    ensure_qapp()
    from equinox.gui.request_panel.panel import RequestPanel
    from equinox.storage import get_db

    db = get_db()
    panel = RequestPanel(db)
    panel.url_input.setText("https://example.com")
    panel._url_valid = True
    panel._body_valid = True

    panel._headers_valid = True
    panel._update_send_button_state()
    assert panel.send_button.isEnabled() is True

    panel._headers_valid = False
    panel._update_send_button_state()
    assert panel.send_button.isEnabled() is False


def test_request_panel_builds_canonical_editor_snapshot(tmp_db_path):
    """The snapshot helper should capture the editor state without Qt types."""
    ensure_qapp()
    from PyQt6.QtWidgets import QTableWidgetItem

    from equinox.core.request import Request
    from equinox.gui.request_panel.panel import RequestPanel
    from equinox.storage import get_db

    class DummyAuth:
        def to_dict(self):
            return {"token": "secret"}

    db = get_db()
    panel = RequestPanel(db)

    panel.method_combo.setCurrentText("POST")
    panel.url_input.setText("https://example.com/api")
    panel.headers_table.set_data({"X-Test": "1"})
    panel.params_table.set_data(
        [
            {"key": "q", "value": "search", "enabled": True},
        ],
    )
    panel.body_type_combo.setCurrentText("GraphQL")
    panel.body_text.setPlainText("raw body")
    panel._gql_query.setPlainText("query { viewer { id } }")
    panel._gql_vars.setPlainText('{"limit": 1}')
    panel.notes_editor.setPlainText("Request notes")
    panel.pre_script_editor.setPlainText("print('pre')")
    panel.post_script_editor.setPlainText("print('post')")
    panel.timeout_spin.setValue(12.5)
    panel.verify_ssl_check.setChecked(False)
    panel.follow_redirects_check.setChecked(False)
    panel.cert_path_input.setText(r"C:\certs\client.crt")
    panel.cert_key_input.setText(r"C:\certs\client.key")
    panel.path_params_table.set_data({"id": "42"})
    panel.set_session_var("token", "abc")

    panel._multipart_table.setRowCount(1)
    panel._multipart_table.setItem(0, 0, QTableWidgetItem("file"))
    panel._multipart_table.setItem(0, 1, QTableWidgetItem("file"))
    panel._multipart_table.setItem(0, 2, QTableWidgetItem(r"C:\tmp\upload.txt"))

    request = Request(method="GET", url="https://example.com")
    request.id = 99
    request.name = "Example"
    request.description = "Existing description"
    request.collection_id = 7
    request.folder = "Folder A"
    panel.current_request = request
    panel._auth = DummyAuth()
    panel._inherited_auth = DummyAuth()
    panel._inherited_auth_source = "collection"

    snapshot = panel._build_request_editor_snapshot()

    assert snapshot.method == "POST"
    assert snapshot.url == "https://example.com/api"
    assert snapshot.headers == {"X-Test": "1"}
    assert snapshot.params == {"q": "search"}
    assert snapshot.params_list == ({"key": "q", "value": "search", "enabled": True},)
    assert snapshot.body == "raw body"
    assert snapshot.body_type == "GraphQL"
    assert snapshot.graphql_query == "query { viewer { id } }"
    assert snapshot.graphql_variables == '{"limit": 1}'
    assert snapshot.multipart_data == (
        {"key": "file", "type": "file", "value": r"C:\tmp\upload.txt"},
    )
    assert snapshot.path_params == {"id": "42"}
    assert snapshot.timeout == 12.5
    assert snapshot.verify_ssl is False
    assert snapshot.follow_redirects is False
    assert snapshot.name == "Example"
    assert snapshot.description == "Request notes"
    assert snapshot.collection_id == 7
    assert snapshot.folder == "Folder A"
    assert snapshot.request_id == 99
    assert snapshot.auth_type == "DummyAuth"
    assert snapshot.auth_data == {"token": "secret"}
    assert snapshot.inherited_auth_type == "DummyAuth"
    assert snapshot.inherited_auth_data == {"token": "secret"}
    assert snapshot.inherited_auth_source == "collection"
    assert snapshot.session_vars == {"token": "abc"}
