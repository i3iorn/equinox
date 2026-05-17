"""Tests for local GUI usage tracking and compact request toolbar behavior."""

from __future__ import annotations

from PyQt6.QtCore import QCoreApplication, QSettings
from PyQt6.QtWidgets import QApplication, QToolButton

_APP = QApplication.instance() or QApplication([])


def _process() -> None:
    QCoreApplication.processEvents()


def test_ui_usage_tracker_persists_counts(tmp_path):
    from equinox.gui.ui_usage_tracker import UIUsageTracker

    settings = QSettings(str(tmp_path / "usage.ini"), QSettings.Format.IniFormat)

    tracker = UIUsageTracker(settings=settings)
    tracker.record("request.send", category="button", context="gui")
    tracker.record("request.send", category="button", context="gui")
    tracker.flush()

    reloaded = UIUsageTracker(settings=settings)
    top = reloaded.top_items(limit=1)

    assert len(top) == 1
    assert top[0]["element_id"] == "request.send"
    assert top[0]["count"] == 2


def test_request_panel_secondary_actions_moved_to_more_menu(tmp_path, monkeypatch):
    from equinox.gui.window import MainWindow
    from equinox.storage import get_db

    monkeypatch.setenv("EQUINOX_DB_PATH", str(tmp_path / "ui_toolbar.db"))
    db = get_db()
    win = MainWindow(db)
    _process()

    more_btn = win.request_panel.findChild(QToolButton, "requestMoreToolsBtn")
    assert more_btn is not None
    assert more_btn.menu() is not None
    action_texts = [a.text() for a in more_btn.menu().actions() if not a.isSeparator()]
    assert "Import from cURL…" in action_texts
    assert "Benchmark…" in action_texts
    assert "Clear Session Vars" in action_texts

    win.close()
    _process()


def test_request_panel_secondary_tools_rank_by_usage(tmp_path, monkeypatch):
    from equinox.gui.window import MainWindow
    from equinox.storage import get_db

    monkeypatch.setenv("EQUINOX_DB_PATH", str(tmp_path / "ui_toolbar_sort.db"))
    db = get_db()
    win = MainWindow(db)
    tracker = win._ui_usage_tracker

    for _ in range(4):
        tracker.record("request.benchmark", category="action", context="panel_action")
    tracker.record("request.import_curl", category="action", context="panel_action")

    win.request_panel._rebuild_secondary_tools_menu()
    more_btn = win.request_panel.findChild(QToolButton, "requestMoreToolsBtn")
    action_texts = [a.text() for a in more_btn.menu().actions() if not a.isSeparator()]

    assert action_texts[0] == "Benchmark…"
    assert action_texts[1] == "Import from cURL…"
    assert action_texts[-1] == "Clear Session Vars"

    win.close()
    _process()


def test_command_palette_items_rank_by_usage(tmp_path, monkeypatch):
    from equinox.gui.window import MainWindow
    from equinox.storage import get_db

    monkeypatch.setenv("EQUINOX_DB_PATH", str(tmp_path / "ui_command_sort.db"))
    db = get_db()
    win = MainWindow(db)
    tracker = win._ui_usage_tracker

    for _ in range(3):
        tracker.record("command.preferences", category="command", context="command_palette")
    tracker.record("command.send_request", category="command", context="command_palette")

    commands = win._command_palette_items()
    assert commands[0]["id"] == "preferences"

    win.close()
    _process()


