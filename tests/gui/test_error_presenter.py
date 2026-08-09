from __future__ import annotations

from unittest.mock import Mock


def test_warning_uses_default_title(monkeypatch):
    from equinox.gui.error_presenter import ErrorPresenter

    calls = []
    monkeypatch.setattr(
        "equinox.gui.error_presenter.QMessageBox.warning",
        lambda parent, title, message: calls.append((parent, title, message)),
    )

    ErrorPresenter.warning(None, "bad input")

    assert calls == [(None, "Warning", "bad input")]


def test_error_with_details_uses_copyable_dialog(monkeypatch):
    from equinox.gui.error_presenter import ErrorPresenter

    calls = []
    monkeypatch.setattr(
        "equinox.gui.error_presenter.CopyableMessageBox.critical",
        lambda parent, title, text, copy_text=None: calls.append((title, text, copy_text)),
    )

    ErrorPresenter.error(None, "failed", details="traceback")

    assert calls == [("Error", "failed", "traceback")]


def test_request_failure_includes_hint_and_log_path(monkeypatch):
    from equinox.gui.error_presenter import ErrorPresenter

    calls = []
    monkeypatch.setattr(
        "equinox.gui.error_presenter.CopyableMessageBox.critical",
        lambda parent, title, text, copy_text=None: calls.append((title, text, copy_text)),
    )

    ErrorPresenter.request_failure(
        None,
        exc_type="TimeoutError",
        message="Request timed out",
        hint="Try a longer timeout",
        details="trace",
        log_file_path="C:/tmp/equinox.log",
    )

    assert len(calls) == 1
    title, text, copy_text = calls[0]
    assert title == "Request Failed - TimeoutError"
    assert "Try a longer timeout" in text
    assert "Full details in: C:/tmp/equinox.log" in text
    assert copy_text == "trace"


def test_warning_with_details_uses_copyable_dialog(monkeypatch):
    from equinox.gui.error_presenter import ErrorPresenter

    calls = []
    monkeypatch.setattr(
        "equinox.gui.error_presenter.CopyableMessageBox.warning",
        lambda parent, title, text, copy_text=None: calls.append((title, text, copy_text)),
    )

    ErrorPresenter.warning(None, "check this", details="extra context")

    assert calls == [("Warning", "check this", "extra context")]


def test_info_with_details_uses_copyable_dialog(monkeypatch):
    from equinox.gui.error_presenter import ErrorPresenter

    calls = []
    monkeypatch.setattr(
        "equinox.gui.error_presenter.CopyableMessageBox.information",
        lambda parent, title, text, copy_text=None: calls.append((title, text, copy_text)),
    )

    ErrorPresenter.info(None, "fyi", details="extra context")

    assert calls == [("Information", "fyi", "extra context")]


def test_info_without_details_uses_plain_message_box(monkeypatch):
    from equinox.gui.error_presenter import ErrorPresenter

    calls = []
    monkeypatch.setattr(
        "equinox.gui.error_presenter.QMessageBox.information",
        lambda parent, title, message: calls.append((parent, title, message)),
    )

    ErrorPresenter.info(None, "fyi")

    assert calls == [(None, "Information", "fyi")]


def test_confirm_returns_true_on_yes(monkeypatch):
    from equinox.gui.error_presenter import ErrorPresenter
    from PyQt6.QtWidgets import QMessageBox

    monkeypatch.setattr(
        "equinox.gui.error_presenter.QMessageBox.question",
        lambda *a, **k: QMessageBox.StandardButton.Yes,
    )

    assert ErrorPresenter.confirm(None, "Proceed?") is True


def test_confirm_returns_false_on_no(monkeypatch):
    from equinox.gui.error_presenter import ErrorPresenter
    from PyQt6.QtWidgets import QMessageBox

    monkeypatch.setattr(
        "equinox.gui.error_presenter.QMessageBox.question",
        lambda *a, **k: QMessageBox.StandardButton.No,
    )

    assert ErrorPresenter.confirm(None, "Proceed?") is False


def test_error_logs_the_message(monkeypatch, caplog):
    import logging

    from equinox.gui.error_presenter import ErrorPresenter

    monkeypatch.setattr("equinox.gui.error_presenter.QMessageBox.critical", lambda *a: None)

    with caplog.at_level(logging.ERROR, logger="equinox.gui.error_presenter"):
        ErrorPresenter.error(None, "something broke")

    assert any("something broke" in record.message for record in caplog.records)


def test_show_status_writes_to_status_bar():
    from equinox.gui.error_presenter import ErrorPresenter

    status = Mock()
    parent = Mock()
    parent.window.return_value = parent
    parent.statusBar.return_value = status

    ErrorPresenter.show_status(parent, "Saved", timeout_ms=1234)

    status.showMessage.assert_called_once_with("Saved", 1234)
