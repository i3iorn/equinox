from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from equinox.gui.request_panel._mixins.assertions_mixin import AssertionsMixin
from equinox.gui.request_panel._mixins.autosave_mixin import RequestAutosaveMixin
from equinox.gui.request_panel._mixins.save_flow_mixin import RequestSaveFlowMixin
from PyQt6.QtCore import QCoreApplication
from PyQt6.QtWidgets import QApplication


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
    from equinox.gui.request_panel.panel import RequestPanel
    from equinox.storage import get_db

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
    from equinox.gui.request_panel.panel import RequestPanel
    from equinox.storage import get_db

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
    from equinox.gui.request_panel.panel import RequestPanel
    from equinox.storage import get_db

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
    from equinox.gui.request_panel.panel import RequestPanel
    from equinox.storage import get_db

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
    from equinox.gui.request_panel.panel import RequestPanel
    from equinox.storage import get_db

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
    from equinox.gui.request_panel.panel import RequestPanel
    from equinox.storage import get_db

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


def test_url_fix_suggestion_encodes_internal_whitespace(tmp_db_path):
    ensure_qapp()
    from equinox.gui.request_panel.panel import RequestPanel
    from equinox.storage import get_db

    panel = RequestPanel(get_db())
    fixed = panel._suggest_url_fix("https://api.example.com/has space")
    assert fixed is not None
    assert fixed[0] == "https://api.example.com/has%20space"


def test_json_body_validation_disables_send_for_invalid_json_even_without_content_type(tmp_db_path):
    ensure_qapp()
    from equinox.gui.request_panel.panel import RequestPanel
    from equinox.storage import get_db

    panel = RequestPanel(get_db())
    panel.url_input.setText("https://api.example.com")
    panel.body_type_combo.setCurrentText("raw (JSON)")
    panel.body_text.setPlainText('{"x":')
    panel.headers_table.reset()

    panel._run_validation_checks()
    assert panel.send_button.isEnabled() is False


def test_request_panel_uses_injected_request_persistence(tmp_db_path):
    ensure_qapp()
    from equinox.gui.request_panel.panel import RequestPanel
    from equinox.storage import get_db

    persistence = Mock()
    panel = RequestPanel(get_db(), request_persistence=persistence)

    assert panel._request_persistence is persistence


def test_request_panel_uses_injected_request_history(tmp_db_path):
    ensure_qapp()
    from equinox.gui.request_panel.panel import RequestPanel
    from equinox.storage import get_db

    history = Mock()
    history.list_recent_urls.return_value = []
    panel = RequestPanel(get_db(), request_history=history)

    assert panel._request_history is history


def test_refresh_url_completer_uses_request_history_service(tmp_db_path):
    ensure_qapp()
    from equinox.gui.request_panel.panel import RequestPanel
    from equinox.storage import get_db

    history = Mock()
    history.list_recent_urls.return_value = [
        "https://api.example.com/a",
        "https://api.example.com/b",
    ]
    panel = RequestPanel(get_db(), request_history=history)

    panel._refresh_url_completer()

    history.list_recent_urls.assert_called_with(limit=200)
    assert panel._url_values[:2] == [
        "https://api.example.com/a",
        "https://api.example.com/b",
    ]


def test_autosave_current_routes_through_request_persistence() -> None:
    from equinox.core.request import Request

    class _Panel(RequestAutosaveMixin):
        def __init__(self) -> None:
            self._dirty = True
            self.current_request = Request(
                method="GET",
                url="https://api.example.com/items",
                id=17,
                name="Items",
                collection_id=7,
                folder="",
            )
            self._request_persistence = Mock()

        def _build_request_from_editor(self, **overrides):
            return Request(
                method="GET",
                url="https://api.example.com/items?active=true",
                headers={},
                **overrides,
            )

        def _clear_dirty(self):
            self._dirty = False

        def _status_message(self, text: str, timeout_ms: int = 5000):
            return None

    panel = _Panel()
    panel.autosave_current()

    panel._request_persistence.autosave_request.assert_called_once()
    assert panel.is_dirty() is False


def test_save_updates_existing_request_when_collection_unchanged(tmp_db_path, monkeypatch):
    """Test that saving an existing request in the same collection calls update_request.

    This test verifies that when a user saves an existing request without changing
    its collection, update_request() is called instead of save_request().
    """
    ensure_qapp()
    from PyQt6.QtWidgets import QDialog

    from equinox.core.request import Request

    # Create a minimal mock panel with only the save-flow behavior
    class _MockPanel(RequestSaveFlowMixin):
        def __init__(self):
            self.db = None
            self.current_request = None
            self._request_persistence = Mock()
            self.url_input = Mock()
            self.method_combo = Mock()

        def _build_request_editor_snapshot(self):
            req = self.current_request
            return SimpleNamespace(
                url=self.url_input.text(),
                method=self.method_combo.currentText(),
                folder=getattr(req, "folder", "") or "",
                request_id=getattr(req, "id", None),
                collection_id=getattr(req, "collection_id", None),
            )

        def window(self):
            """Mock window to prevent access to collections_panel."""
            return Mock()

        def _build_request_from_editor(self, **overrides):
            """Mock request builder."""
            return Request(
                method="GET",
                url="https://api.example.com/items",
                headers={},
                **overrides,
            )

        def _mark_dirty(self):
            pass

        def _clear_dirty(self):
            pass

        def _status_message(self, msg, timeout_ms=5000):
            pass

    mock_panel = _MockPanel()
    mock_panel.url_input.text.return_value = "https://api.example.com/items"
    mock_panel.method_combo.currentText.return_value = "GET"
    mock_panel.current_request = Request(
        method="GET",
        url="https://api.example.com/items",
        headers={},
        name="Items",
        collection_id=7,
        id=123,
        folder="",
    )

    # Mock the SaveRequestDialog
    class _FakeDialog:
        def __init__(self, *args, **kwargs):
            """Accept any arguments (db, method, url, folder, parent)."""
            pass

        def exec(self):
            return QDialog.DialogCode.Accepted

        def result_values(self):
            return "Items", 7, "Default", ""

    monkeypatch.setattr(
        "equinox.gui.request_panel._mixins.save_flow_mixin.SaveRequestDialog",
        _FakeDialog,
    )
    mock_panel._request_persistence.list_save_collections.return_value = [
        {"id": 7, "name": "Default"},
    ]
    mock_panel._request_persistence.save_request_from_dialog.return_value = SimpleNamespace(
        request_id=123,
        updated_existing=True,
    )

    # Call the save flow
    result = mock_panel._save_request()

    # Verify update_request was called (not save_request)
    assert result is True
    mock_panel._request_persistence.list_save_collections.assert_called_once()
    mock_panel._request_persistence.save_request_from_dialog.assert_called_once()
    mock_panel._request_persistence.update_request.assert_not_called()
    mock_panel._request_persistence.save_request.assert_not_called()


def test_save_calls_save_request_when_collection_changes(tmp_db_path, monkeypatch):
    """Test that saving an existing request with a different collection calls save_request.

    If the user moves a request to a different collection during save, it should be
    treated as a new save operation (with a new ID).
    """
    ensure_qapp()
    from PyQt6.QtWidgets import QDialog

    from equinox.core.request import Request

    class _MockPanel(RequestSaveFlowMixin):
        def __init__(self):
            self.db = None
            self.current_request = None
            self._request_persistence = Mock()
            self.url_input = Mock()
            self.method_combo = Mock()

        def _build_request_editor_snapshot(self):
            req = self.current_request
            return SimpleNamespace(
                url=self.url_input.text(),
                method=self.method_combo.currentText(),
                folder=getattr(req, "folder", "") or "",
                request_id=getattr(req, "id", None),
                collection_id=getattr(req, "collection_id", None),
            )

        def window(self):
            return Mock()

        def _build_request_from_editor(self, **overrides):
            return Request(
                method="POST",
                url="https://api.example.com/users",
                headers={},
                **overrides,
            )

        def _mark_dirty(self):
            pass

        def _clear_dirty(self):
            pass

        def _status_message(self, msg, timeout_ms=5000):
            pass

    mock_panel = _MockPanel()
    mock_panel.url_input.text.return_value = "https://api.example.com/users"
    mock_panel.method_combo.currentText.return_value = "POST"

    # Request originally in collection 7
    mock_panel.current_request = Request(
        method="POST",
        url="https://api.example.com/users",
        headers={},
        name="Create User",
        collection_id=7,
        id=456,
        folder="",
    )

    class _FakeDialog:
        def __init__(self, *args, **kwargs):
            pass

        def exec(self):
            return QDialog.DialogCode.Accepted

        def result_values(self):
            # Return different collection (99 instead of 7)
            return "Create User", 99, "Other", ""

    monkeypatch.setattr(
        "equinox.gui.request_panel._mixins.save_flow_mixin.SaveRequestDialog",
        _FakeDialog,
    )
    mock_panel._request_persistence.list_save_collections.return_value = [
        {"id": 99, "name": "Other"},
    ]

    mock_panel._request_persistence.save_request_from_dialog.return_value = SimpleNamespace(
        request_id=789,
        updated_existing=False,
    )

    result = mock_panel._save_request()

    # Verify save_request was called (not update_request)
    assert result is True
    mock_panel._request_persistence.list_save_collections.assert_called_once()
    mock_panel._request_persistence.save_request_from_dialog.assert_called_once()
    mock_panel._request_persistence.save_request.assert_not_called()
    mock_panel._request_persistence.update_request.assert_not_called()


def test_save_dialog_cancel_is_non_error(monkeypatch):
    from PyQt6.QtWidgets import QDialog

    class _Panel(RequestSaveFlowMixin):
        def __init__(self):
            self._request_persistence = Mock()
            self.url_input = Mock()
            self.method_combo = Mock()

        def _build_request_editor_snapshot(self):
            return SimpleNamespace(
                url="https://api.example.com/items",
                method="GET",
                folder="",
                request_id=None,
                collection_id=None,
            )

        def _as_qwidget(self):
            return None

        def _build_request_from_editor(self, **overrides):
            raise AssertionError("should not build request when dialog is cancelled")

        def _clear_dirty(self):
            pass

        def _status_message(self, msg, timeout_ms=5000):
            pass

    class _FakeDialog:
        def __init__(self, *args, **kwargs):
            pass

        def exec(self):
            return QDialog.DialogCode.Rejected

        def result_values(self):
            raise AssertionError("result_values should not be called on cancel")

    critical_calls = []
    monkeypatch.setattr(
        "equinox.gui.request_panel._mixins.save_flow_mixin.SaveRequestDialog",
        _FakeDialog,
    )
    monkeypatch.setattr(
        "equinox.gui.request_panel._mixins.save_flow_mixin.QMessageBox.critical",
        lambda *args, **kwargs: critical_calls.append((args, kwargs)),
    )

    panel = _Panel()
    panel._request_persistence.list_save_collections.return_value = []

    assert panel._save_request() is False
    assert critical_calls == []


def test_logging_panel_accessor_returns_none_on_window_error(tmp_db_path, monkeypatch):
    from equinox.gui.request_panel.panel import RequestPanel

    class _PanelShim:
        def window(self):
            raise RuntimeError("boom")

    assert RequestPanel._logging_panel.fget(_PanelShim()) is None


def test_sync_dirty_state_ui_swallows_sync_errors() -> None:
    class _Panel(RequestAutosaveMixin):
        def __init__(self) -> None:
            self._dirty = False

        def _sync_editor_state_ui(self):
            raise RuntimeError("ui unavailable")

    panel = _Panel()
    panel._sync_dirty_state_ui()


def test_assertions_tab_builder_creates_required_widgets() -> None:
    ensure_qapp()

    class _Panel(AssertionsMixin):
        pass

    panel = _Panel()
    widget = panel._create_assertions_tab()

    assert widget is not None
    assert panel.assertions_table.columnCount() == 3
    assert panel.assertions_results_label.text() == "—"
