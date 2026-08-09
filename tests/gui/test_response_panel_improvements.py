import json

import pytest
from equinox.core.request import Request
from equinox.core.request import Response
from equinox.gui.response_panel import ResponsePanel
from equinox.gui.response_panel._formatting import parse_cookies
from equinox.gui.response_panel.intelligence_panel import _AUDIT_MAX_LINES
from equinox.gui.response_panel.intelligence_panel import IntelligencePanel
from equinox.gui.response_panel.pretty_print import PrettyPrintRunnable
from equinox.gui.response_panel.search import SearchBar
from PyQt6.QtWidgets import QApplication
from PyQt6.QtWidgets import QTextEdit


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


def test_large_body_over_hard_cap_disables_full_render_button() -> None:
    panel = ResponsePanel()
    panel._LARGE_BODY_THRESHOLD = 64
    panel._MAX_RENDER_BODY_SIZE = 128
    resp = _mk_response(b"x" * 256, "application/json")

    panel.display_response(resp)

    assert panel._body_warning.isHidden() is False
    assert panel._body_load_btn.isEnabled() is False


def test_load_large_body_over_hard_cap_uses_preview_only() -> None:
    panel = ResponsePanel()
    panel._MAX_RENDER_BODY_SIZE = 128
    panel._LARGE_BODY_PREVIEW_BYTES = 32
    resp = _mk_response(b"a" * 256, "application/json")
    panel.current_response = resp

    panel._load_large_body()

    text = panel.body_text.toPlainText()
    assert "Preview only" in text
    assert "omitted" in text


def test_display_body_dispatches_pretty_print_off_thread(monkeypatch) -> None:
    """Pretty-printing a normal (under-threshold) body must run on the
    thread pool, not synchronously inline — regression test for a body
    render that could stall the UI thread on every response."""
    panel = ResponsePanel()
    resp = _mk_response(json.dumps({"a": 1}).encode(), "application/json")

    called = {"start": False}

    class _Pool:
        @staticmethod
        def start(runnable):
            called["start"] = True
            assert isinstance(runnable, PrettyPrintRunnable)

    panel._thread_pool = _Pool()
    panel.current_response = resp
    panel._display_body(resp)

    assert called["start"] is True


def test_display_body_renders_correctly_after_async_roundtrip() -> None:
    """End-to-end: the real thread pool must still produce correct,
    pretty-printed content in body_text once the background job completes."""
    from PyQt6.QtCore import QCoreApplication
    from PyQt6.QtCore import QThreadPool

    panel = ResponsePanel()
    resp = _mk_response(json.dumps({"a": 1}).encode(), "application/json")
    panel.current_response = resp

    panel._display_body(resp)
    QThreadPool.globalInstance().waitForDone(2000)
    for _ in range(20):
        QCoreApplication.processEvents()

    text = panel.body_text.toPlainText()
    assert '"a"' in text
    assert "1" in text


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


def test_searchbar_moves_editor_to_first_and_next_match() -> None:
    editor = QTextEdit()
    editor.setPlainText("line-0\nline-1 needle\nline-2\nline-3 needle\n")
    bar = SearchBar(editor)

    first = editor.toPlainText().index("needle")
    second = editor.toPlainText().index("needle", first + 1)

    bar._start_search_job("needle")

    assert bar._current_idx == 0
    assert editor.textCursor().position() == first

    bar._find_next()
    assert editor.textCursor().position() == second


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


def test_response_panel_shows_content_type_summary() -> None:
    panel = ResponsePanel()
    resp = _mk_response(b'{"ok": true}', "application/json; charset=utf-8")

    panel.display_response(resp)

    content_type_label = panel.content_type_label
    assert content_type_label.text() == "application/json"
    assert "charset=utf-8" in content_type_label.toolTip()


def test_response_panel_header_filter_count_is_explicit() -> None:
    panel = ResponsePanel()
    resp = Response(
        status_code=200,
        reason="OK",
        headers={"content-type": "application/json", "x-request-id": "abc123"},
        body=b'{"ok": true}',
        elapsed=0.1,
        request=Request(method="GET", url="https://example.com/api"),
    )

    panel.display_response(resp)
    assert panel._hdrs_count_label.text() == "2 headers"

    panel._on_hdrs_filter_changed("missing-header")
    assert panel._hdrs_count_label.text() == "0 of 2 shown"


def test_response_panel_restores_last_active_tab() -> None:
    from equinox.gui.ui_common import get_gui_settings

    settings = get_gui_settings()
    settings.remove("response/active_tab")
    settings.sync()

    first = ResponsePanel()
    target_idx = next(
        idx for idx in range(first.tabs.count()) if first.tabs.tabText(idx).startswith("Headers")
    )
    first.tabs.setCurrentIndex(target_idx)

    second = ResponsePanel()
    assert second.tabs.tabText(second.tabs.currentIndex()).startswith("Headers")

    settings.remove("response/active_tab")
    settings.sync()


def test_audit_tail_reader_is_bounded(tmp_path) -> None:
    audit = tmp_path / "audit.log"
    with audit.open("w", encoding="utf-8") as fh:
        for i in range(2_000):
            fh.write(
                json.dumps({"event_type": "validation_failure", "message": f"event-{i}"}) + "\n",
            )

    lines = IntelligencePanel._read_recent_audit_lines(audit)
    assert len(lines) <= _AUDIT_MAX_LINES
    assert any("event-1999" in line for line in lines)
