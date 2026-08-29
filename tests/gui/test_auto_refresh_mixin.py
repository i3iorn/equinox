"""Contract tests for the shared AutoRefreshMixin.

History and Collections each carried their own copy of this timer logic
before they were unified. The copies had already drifted: one derived the
enabled flag with ``bool(state)``, the other by comparing against
``Qt.CheckState.Checked``. These tests run the same expectations against
every panel using the mixin, so a future third panel inherits the contract
rather than a fourth transcription of it.
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import QCoreApplication, Qt
from PyQt6.QtWidgets import QApplication

_APP = QApplication.instance() or QApplication([])


def _process():
    QCoreApplication.processEvents()


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("EQUINOX_DB_PATH", str(tmp_path / "test.db"))
    from equinox.storage import get_db

    return get_db()


def _history_panel(db):
    from equinox.gui.history_panel import HistoryPanel

    return HistoryPanel(db)


def _collections_panel(db):
    from equinox.gui.collection_panel import CollectionsPanel

    return CollectionsPanel(db)


_PANEL_FACTORIES = [
    pytest.param(_history_panel, id="history"),
    pytest.param(_collections_panel, id="collections"),
]


@pytest.fixture(params=_PANEL_FACTORIES)
def panel(request, db):
    widget = request.param(db)
    yield widget
    widget.close()


def test_mixin_supplies_the_auto_refresh_behavior(panel):
    """The panels must inherit this, not define their own copy."""
    from equinox.gui.ui_common import AutoRefreshMixin

    assert isinstance(panel, AutoRefreshMixin)
    for name in ("_setup_auto_refresh", "_refresh_if_visible", "_toggle_auto_refresh"):
        owner = next(cls for cls in type(panel).__mro__ if name in cls.__dict__)
        assert owner is AutoRefreshMixin, (
            f"{type(panel).__name__} overrides {name} instead of using the shared mixin"
        )


def test_timer_starts_running_on_construction(panel):
    from equinox.gui.ui_common import AUTO_REFRESH_INTERVAL_MS

    assert panel.refresh_timer.isActive()
    assert panel.refresh_timer.interval() == AUTO_REFRESH_INTERVAL_MS
    assert panel.auto_refresh_enabled is True


def test_unchecking_stops_the_timer_and_rechecking_restarts_it(panel):
    panel.auto_refresh_checkbox.setChecked(False)
    _process()
    assert panel.auto_refresh_enabled is False
    assert not panel.refresh_timer.isActive()

    panel.auto_refresh_checkbox.setChecked(True)
    _process()
    assert panel.auto_refresh_enabled is True
    assert panel.refresh_timer.isActive()


def test_partially_checked_counts_as_disabled(panel):
    """The drift this refactor resolved: PartiallyChecked is 1, so a plain
    truthiness test read it as enabled while the other copy did not.
    """
    panel._toggle_auto_refresh(Qt.CheckState.PartiallyChecked.value)
    _process()

    assert panel.auto_refresh_enabled is False
    assert not panel.refresh_timer.isActive()


def test_refresh_is_skipped_while_the_panel_is_hidden(panel, monkeypatch):
    """Hidden sidebar tabs must not pay for a refresh they cannot show."""
    calls = []
    monkeypatch.setattr(type(panel), "refresh", lambda self: calls.append(1))

    assert not panel.isVisible()
    panel._refresh_if_visible()
    assert calls == []

    panel.show()
    _process()
    try:
        panel._refresh_if_visible()
        assert calls == [1]
    finally:
        panel.hide()
