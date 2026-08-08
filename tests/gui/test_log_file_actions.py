from __future__ import annotations

from pathlib import Path

import pytest

from equinox.gui.log_file_actions import (
    LogOpenResult,
    LogOpenStatus,
    show_log_file_open_result,
    try_open_current_log_file,
)


def test_try_open_current_log_file_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("equinox.gui.log_file_actions.get_log_file", lambda: None)

    result = try_open_current_log_file()

    assert result.status == LogOpenStatus.MISSING


def test_try_open_current_log_file_rejects_non_log(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    bad = tmp_path / "not_a_log.txt"
    bad.write_text("x", encoding="utf-8")
    monkeypatch.setattr("equinox.gui.log_file_actions.get_log_file", lambda: bad)

    result = try_open_current_log_file()

    assert result.status == LogOpenStatus.INVALID_PATH
    assert result.resolved_path == bad.resolve()


def test_try_open_current_log_file_opened(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    log = tmp_path / "equinox.log"
    log.write_text("line", encoding="utf-8")
    monkeypatch.setattr("equinox.gui.log_file_actions.get_log_file", lambda: log)

    opened = {"count": 0}

    def _fake_open(path: Path) -> None:
        assert path == log.resolve()
        opened["count"] += 1

    monkeypatch.setattr("equinox.gui.log_file_actions.open_path_in_os", _fake_open)

    result = try_open_current_log_file()

    assert result.status == LogOpenStatus.OPENED
    assert opened["count"] == 1


def test_try_open_current_log_file_open_failed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    log = tmp_path / "equinox.log"
    log.write_text("line", encoding="utf-8")
    monkeypatch.setattr("equinox.gui.log_file_actions.get_log_file", lambda: log)

    def _boom(_path: Path) -> None:
        raise OSError("no opener")

    monkeypatch.setattr("equinox.gui.log_file_actions.open_path_in_os", _boom)

    result = try_open_current_log_file()

    assert result.status == LogOpenStatus.OPEN_FAILED
    assert "no opener" in (result.error or "")


def test_show_log_file_open_result_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    monkeypatch.setattr(
        "equinox.gui.log_file_actions.QMessageBox.information",
        lambda *args: calls.append(args),
    )

    opened = show_log_file_open_result(
        None,
        LogOpenResult(status=LogOpenStatus.MISSING),
        "No log file yet.",
    )

    assert opened is False
    assert calls
    assert calls[0][2] == "No log file yet."


def test_show_log_file_open_result_open_failed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls = []
    log = tmp_path / "equinox.log"
    log.write_text("x", encoding="utf-8")

    monkeypatch.setattr(
        "equinox.gui.log_file_actions.QMessageBox.information",
        lambda *args: calls.append(args),
    )

    opened = show_log_file_open_result(
        None,
        LogOpenResult(status=LogOpenStatus.OPEN_FAILED, log_path=log, error="boom"),
        "ignored",
    )

    assert opened is False
    assert calls
    assert "boom" in calls[0][2]
