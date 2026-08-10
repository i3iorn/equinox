"""Regression test for issue #5: body search state survives a history switch.

Loading a new response only reset the headers filter, not the body search
bar - its query text, match count, and highlights kept pointing at the
previous response's body after switching entries.
"""

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
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_body_search_clears_when_a_new_response_is_displayed():
    panel = ResponsePanel()
    panel._view_preference = "raw"

    panel.display_response(DummyResp({"needle": "first-response-only"}))
    # Body pretty-printing runs off-thread (see display_mixin._display_body);
    # setting the target's text directly isolates this test to the reset
    # behaviour under test rather than that async pipeline.
    panel.body_text.setPlainText('{"needle": "first-response-only"}')

    search_bar = panel._search_bar
    search_bar.show_and_focus()
    search_bar._input.setText("needle")
    search_bar._on_debounced()  # small doc runs synchronously

    assert search_bar._offsets, "search should have found a match to begin with"
    assert search_bar._input.text() == "needle"

    panel.display_response(DummyResp({"other": "second-response"}))

    assert search_bar._input.text() == ""
    assert search_bar._offsets == []
    assert search_bar._current_idx == -1
    assert search_bar._target.extraSelections() == []
    assert search_bar._match_label.text() == ""


def test_body_search_clears_even_when_hidden():
    """The bar doesn't have to be visible for its state to go stale."""
    panel = ResponsePanel()
    panel._view_preference = "raw"

    panel.display_response(DummyResp({"needle": "first-response-only"}))
    panel.body_text.setPlainText('{"needle": "first-response-only"}')

    search_bar = panel._search_bar
    search_bar._input.setText("needle")
    search_bar._on_debounced()
    assert search_bar._offsets

    panel.display_response(DummyResp({"other": "second"}))

    assert search_bar._input.text() == ""
    assert search_bar._offsets == []
