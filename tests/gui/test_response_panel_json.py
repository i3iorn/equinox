import json

import pytest

from PyQt6.QtWidgets import QApplication

from equinox.gui.response_panel import ResponsePanel


class DummyReq:
    def __init__(self):
        self.url = "http://example.com/api"
        self.method = "GET"
        self.headers = {}
        self.params = {}
        self.body = None


class DummyResp:
    def __init__(self, obj):
        self._obj = obj
        self.status_code = 200
        self.reason = "OK"
        self.elapsed = 0.05
        self._text = json.dumps(obj)
        self.size = len(self._text)
        self.headers = {"content-type": "application/json"}
        self.request = DummyReq()
        self.sent_url = ""
        self.sent_headers = {}

    @property
    def text(self):
        return self._text

    def json(self):
        return self._obj

    @property
    def is_json(self):
        return True


@pytest.fixture(autouse=True)
def ensure_qapp():
    # Ensure a QApplication exists for the widget tests
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_json_tree_populates_and_view_toggle():
    panel = ResponsePanel()

    obj = {"a": 1, "b": {"c": [1, 2, 3]}}
    resp = DummyResp(obj)

    # Display the response — should populate body and JSON tree
    panel.display_response(resp)

    # JSON tree should be populated (placeholder hidden)
    assert not panel._json_tree._placeholder.isVisible()
    assert panel._view_json_act.isEnabled()

    # Switch to JSON view and confirm tab switched
    panel._on_view_selected("json")
    assert panel._view_json_act.isChecked()
    assert panel.tabs.tabText(panel.tabs.currentIndex()) == "JSON"

    # Switch back to raw view
    panel._on_view_selected("raw")
    assert panel._view_raw_act.isChecked()
    assert panel.tabs.tabText(panel.tabs.currentIndex()) == "Body"

    # Exercise expand/collapse copy handlers
    panel._json_tree._on_expand_all()
    panel._json_tree._on_collapse_all()
    # Copy shouldn't raise
    panel._json_tree._on_copy_json()

