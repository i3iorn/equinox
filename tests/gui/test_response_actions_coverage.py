from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import QApplication

from equinox.core.request import Request, Response
from equinox.gui.response_panel import ResponsePanel


@pytest.fixture
def app():
    app = QApplication.instance()
    if not app:
        app = QApplication([])
    return app


@pytest.fixture
def panel(app):
    return ResponsePanel()


def test_get_body_text_from_widget(panel):
    panel.body_text.setPlainText("hello world")
    assert panel._get_body_text() == "hello world"


def test_get_body_text_from_response(panel):
    panel.body_text.setPlainText("")
    req = Request(method="GET", url="http://test.com")
    resp = Response(
        status_code=200,
        reason="OK",
        headers={"content-type": "application/json"},
        body=b'{"foo": "bar"}',
        request=req,
        elapsed=0.1,
    )
    panel.current_response = resp
    # pretty_print_body should be called
    assert "foo" in panel._get_body_text()


def test_suggest_filename(panel):
    assert panel._suggest_filename() == "response.txt"

    req = Request(method="GET", url="http://test.com")
    resp = Response(
        status_code=200,
        reason="OK",
        headers={"content-type": "application/json"},
        body=b"{}",
        request=req,
        elapsed=0.1,
    )
    panel.current_response = resp
    assert panel._suggest_filename() == "response.json"

    resp.headers["content-type"] = "text/xml"
    assert panel._suggest_filename() == "response.xml"


def test_copy_body(panel):
    panel.body_text.setPlainText("clipboard content")
    with patch.object(QGuiApplication, "clipboard") as mock_clipboard:
        clipboard = MagicMock()
        mock_clipboard.return_value = clipboard
        panel._copy_body()
        clipboard.setText.assert_called_with("clipboard content")


def test_toggle_word_wrap(panel):
    panel._toggle_word_wrap(True)
    assert panel.body_text.lineWrapMode() == panel.body_text.LineWrapMode.WidgetWidth
    panel._toggle_word_wrap(False)
    assert panel.body_text.lineWrapMode() == panel.body_text.LineWrapMode.NoWrap


def test_download_body(panel):
    req = Request(method="GET", url="http://test.com")
    resp = Response(
        status_code=200,
        reason="OK",
        headers={},
        body=b"save me",
        request=req,
        elapsed=0.1,
    )
    panel.current_response = resp
    panel.body_text.setPlainText("save me")

    with patch(
        "equinox.gui.response_panel.actions_mixin.QFileDialog.getSaveFileName",
        return_value=("test.txt", "Text (*.txt)"),
    ):
        with patch(
            "equinox.gui.response_panel.actions_mixin.validate_selected_path",
            return_value="test.txt",
        ) as mock_validate:
            with patch("equinox.gui.response_panel.actions_mixin.atomic_write_bytes") as mock_write:
                panel._download_body()
                mock_validate.assert_called_with("test.txt", must_exist=False)
                mock_write.assert_called_with("test.txt", b"save me")


def test_copy_as_curl(panel):
    req = Request(method="GET", url="http://test.com", headers={"X-Test": "Value"})
    resp = Response(status_code=200, reason="OK", headers={}, body=b"", request=req, elapsed=0.1)
    panel.current_response = resp

    with patch.object(QGuiApplication, "clipboard") as mock_clipboard:
        clipboard = MagicMock()
        mock_clipboard.return_value = clipboard
        panel._copy_as_curl()
        # Should contain the URL
        args, _ = clipboard.setText.call_args
        assert "http://test.com" in args[0]
        assert "curl" in args[0].lower()


def test_diff_with_history_no_db(panel):
    # If no DB, _fetch_history_entries returns []
    # And then _diff_with_history shows an INFORMATION box (not warning)
    panel._get_database = MagicMock(return_value=None)
    with patch("equinox.gui.error_presenter.QMessageBox.information") as mock_info:
        panel.current_response = MagicMock()
        panel._diff_with_history()
        mock_info.assert_called()


def test_diff_with_history_empty(panel):
    db = MagicMock()
    panel._get_database = MagicMock(return_value=db)
    panel.current_response = MagicMock()
    panel.current_response.request.url = "http://test.com"
    panel.current_response.request.method = "GET"

    # Mock HistoryManager to return empty list
    with patch("equinox.storage.history.manager.HistoryManager.search_history") as mock_search:
        mock_search.return_value = []
        with patch("equinox.gui.error_presenter.QMessageBox.information") as mock_info:
            panel._diff_with_history()
            mock_info.assert_called_with(
                panel,
                "Diff vs. History",
                "No matching history entries found for this request.",
            )


def test_diff_with_history_uses_injected_facade(app):
    """ResponsePanel must route history lookups through RequestHistoryService,
    not construct HistoryManager/Database directly (architecture-boundary
    regression test)."""
    from equinox.application.requests import RequestHistoryService

    facade = MagicMock(spec=RequestHistoryService)
    facade.search_recent.return_value = [
        {"url": "http://test.com", "method": "GET", "response_body": "old"},
    ]
    panel = ResponsePanel(request_history=facade)
    panel.current_response = MagicMock()
    panel.current_response.request.url = "http://test.com"
    panel.current_response.request.method = "GET"

    entries = panel._fetch_history_entries()

    facade.search_recent.assert_called_once_with(query="http://test.com", method="GET", limit=30)
    assert entries == [{"url": "http://test.com", "method": "GET", "response_body": "old"}]


def test_fetch_history_entries_builds_facade_lazily_when_not_injected(panel):
    """When no facade was injected (e.g. a bare ResponsePanel()), a fallback
    RequestHistoryService must be built from the window's db rather than a
    raw HistoryManager — and reused on subsequent calls."""
    db = MagicMock()
    panel._get_database = MagicMock(return_value=db)
    panel.current_response = MagicMock()
    panel.current_response.request.url = "http://test.com"
    panel.current_response.request.method = "GET"

    with patch("equinox.storage.history.manager.HistoryManager.search_history", return_value=[]):
        panel._fetch_history_entries()

    from equinox.application.requests import RequestHistoryService

    assert isinstance(panel._request_history, RequestHistoryService)
    panel._get_database.assert_called_once()

    # A second call must reuse the cached facade instead of rebuilding it.
    with patch("equinox.storage.history.manager.HistoryManager.search_history", return_value=[]):
        panel._fetch_history_entries()
    panel._get_database.assert_called_once()
