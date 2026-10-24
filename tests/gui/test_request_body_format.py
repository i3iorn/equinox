"""Request-body formatting gate on the GUI thread (issue #76).

Pretty-printing a ~100 KB JSON body costs 150 ms+ of main-thread CPU per
response display. The 512 KB gate skips the parse/pretty-print round-trip
for very large bodies and returns the raw text instead.
"""

from __future__ import annotations

import json

from equinox.gui.response_panel.display_mixin import ResponseDisplayMixin


def test_small_json_body_is_pretty_printed():
    body = json.dumps({"alpha": 1, "nested": {"b": [1, 2, 3]}}, separators=(",", ":"))

    result = ResponseDisplayMixin._format_request_body(body)

    assert result == json.dumps(json.loads(body), indent=2, ensure_ascii=False)


def test_large_json_body_skips_pretty_print(monkeypatch):
    # A body above the gate must not reach json.loads/dumps at all.
    large = json.dumps(
        {"data": "x" * (ResponseDisplayMixin._REQUEST_BODY_PRETTY_PRINT_MAX_LEN + 1)},
    )

    import equinox.gui.response_panel.display_mixin as mod

    def _boom(*args, **kwargs):
        raise AssertionError("json.loads must not run for gated bodies")

    monkeypatch.setattr(mod.json, "loads", _boom)

    result = ResponseDisplayMixin._format_request_body(large)

    assert result == large  # raw text, unmodified


def test_invalid_json_body_returns_raw_text():
    body = "not json at all {"

    assert ResponseDisplayMixin._format_request_body(body) == body


def test_dict_body_is_pretty_printed():
    result = ResponseDisplayMixin._format_request_body({"a": 1})

    assert result == json.dumps({"a": 1}, indent=2, ensure_ascii=False)
