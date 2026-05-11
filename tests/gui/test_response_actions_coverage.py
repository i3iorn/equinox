from unittest.mock import MagicMock, patch
import pytest
from PyQt6.QtWidgets import QApplication
from equinox.gui.response_panel import ResponsePanel
from equinox.core.request import Request, Response

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
        elapsed=0.1
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
        body=b'{}',
        request=req,
        elapsed=0.1
    )
    panel.current_response = resp
    assert panel._suggest_filename() == "response.json"

    resp.headers["content-type"] = "text/xml"
    assert panel._suggest_filename() == "response.xml"

def test_copy_body(panel):
    panel.body_text.setPlainText("clipboard content")
    with patch.object(QApplication, 'clipboard') as mock_clipboard:
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
    resp = Response(status_code=200, reason="OK", headers={}, body=b"save me", request=req, elapsed=0.1)
    panel.current_response = resp
    panel.body_text.setPlainText("save me")
    
    # Try patching builtins.open directly since it's used in the mixin
    with patch("equinox.gui.response_panel.actions_mixin.QFileDialog.getSaveFileName", return_value=("test.txt", "Text (*.txt)")):
        with patch("builtins.open", MagicMock()) as mock_open:
            mock_file = MagicMock()
            mock_open.return_value.__enter__.return_value = mock_file
            panel._download_body()
            mock_open.assert_called_with("test.txt", "wb")
            mock_file.write.assert_called_with(b"save me")

def test_copy_as_curl(panel):
    req = Request(method="GET", url="http://test.com", headers={"X-Test": "Value"})
    resp = Response(status_code=200, reason="OK", headers={}, body=b"", request=req, elapsed=0.1)
    panel.current_response = resp
    
    with patch.object(QApplication, 'clipboard') as mock_clipboard:
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
    with patch("equinox.gui.response_panel.actions_mixin.QMessageBox.information") as mock_info:
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
        with patch("equinox.gui.response_panel.actions_mixin.QMessageBox.information") as mock_info:
            panel._diff_with_history()
            mock_info.assert_called_with(panel, "Diff vs. History", "No matching history entries found for this request.")
