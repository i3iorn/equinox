import json

import pytest
from PyQt6.QtWidgets import QApplication, QTextEdit

from equinox.core.request import Request, Response
from equinox.gui.response_panel import ResponsePanel
from equinox.gui.response_panel._formatting import parse_cookies
from equinox.gui.response_panel.intelligence_panel import IntelligencePanel, _AUDIT_MAX_LINES
from equinox.gui.response_panel.pretty_print import PrettyPrintRunnable
from equinox.gui.response_panel.search_bar import SearchBar


@pytest.fixture(autouse=True)
def ensure_qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _mk_response(body: bytes, content_type: str = "application/json") -> Response:
    req = Request(method="GET", url="https://example.com/api")
    return Response(
        status_code=200,
        reason="OK",
        headers={"content-type": content_type},
        body=body,
        elapsed=0.1,
        request=req,
    )


def test_decode_response_body_uses_response_charset() -> None:
    panel = ResponsePanel()
    resp = _mk_response(b"caf\xe9", "text/plain; charset=iso-8859-1")
    assert panel._decode_response_body(resp) == "café"


def test_decode_response_body_binary_payload_placeholder() -> None:
    panel = ResponsePanel()
    resp = _mk_response(b"\x00\x01\x02", "application/octet-stream")
    text = panel._decode_response_body(resp)
    assert "Binary response omitted" in text
    assert "3 bytes" in text


def test_parse_cookies_supports_repeated_set_cookie_headers() -> None:
    headers = [
        ("Set-Cookie", "a=1; Path=/"),
        ("set-cookie", "b=2; HttpOnly"),
    ]
    cookies = parse_cookies(headers)
    names = [name for name, _ in cookies]
    assert "a" in names
    assert "b" in names


def test_pretty_print_runnable_falls_back_to_raw_text(monkeypatch) -> None:
    resp = _mk_response(b"raw-fallback", "text/plain")

    def _boom(_response):
        raise RuntimeError("pretty fail")

    import equinox.gui.response_panel.pretty_print as pretty_mod

    monkeypatch.setattr(pretty_mod, "pretty_print_body", _boom)
    runnable = PrettyPrintRunnable(resp, "marker")
    assert runnable._format_safely() == "raw-fallback"


def test_render_pipeline_warns_when_subsection_fails(monkeypatch) -> None:
    panel = ResponsePanel()
    resp = _mk_response(json.dumps({"ok": True}).encode("utf-8"), "application/json")

    def _boom(_response):
        raise RuntimeError("tab rendering failed")

    monkeypatch.setattr(panel, "_populate_all_tabs", _boom)
    panel.display_response(resp)

    assert panel._render_warning_label.text()
    assert "failed to render" in panel._render_warning_label.text().lower()


def test_searchbar_dispatches_async_for_large_document(monkeypatch) -> None:
    editor = QTextEdit()
    editor.setPlainText("A" * 30_000)
    bar = SearchBar(editor)

    called = {"start": False}

    class _Pool:
        @staticmethod
        def start(_runnable):
            called["start"] = True

    bar._thread_pool = _Pool()
    bar._start_search_job("AAAA")

    assert called["start"] is True


def test_jsonpath_mode_does_not_emit_text_offsets() -> None:
    editor = QTextEdit()
    editor.setPlainText('{"users":[{"name":"Alice"},{"name":"Alice"}]}')
    bar = SearchBar(editor)
    bar.set_json_doc({"users": [{"name": "Alice"}, {"name": "Alice"}]})
    bar.show_and_focus()
    bar._jp_btn.setChecked(True)
    bar._input.setText("$.users[*].name")

    # JSONPath mode no longer maps values to ambiguous text offsets.
    assert bar._offsets == []


def test_status_and_method_labels_apply_color_styles() -> None:
    panel = ResponsePanel()
    resp = _mk_response(b'{"ok": true}', "application/json")
    resp.request.method = "GET"

    panel.display_response(resp)

    assert "color:" in panel.status_label.styleSheet()
    assert "color:" in panel.sent_method_label.styleSheet()


def test_audit_tail_reader_is_bounded(tmp_path) -> None:
    audit = tmp_path / "audit.log"
    with audit.open("w", encoding="utf-8") as fh:
        for i in range(2_000):
            fh.write(json.dumps({"event_type": "validation_failure", "message": f"event-{i}"}) + "\n")

    lines = IntelligencePanel._read_recent_audit_lines(audit)
    assert len(lines) <= _AUDIT_MAX_LINES
    assert any("event-1999" in line for line in lines)


