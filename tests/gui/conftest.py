"""Shared fixtures for the GUI test suite.

The `db` fixture below was previously re-typed, byte-identical, in nine
separate GUI test modules. It lives here so a change to how tests get a
database is made once.

The QApplication itself is built by the root tests/conftest.py; see
`gui_helpers` for the event-loop pump that goes with it.
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import QSettings


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """Return a Database backed by a throwaway per-test SQLite file."""
    monkeypatch.setenv("EQUINOX_DB_PATH", str(tmp_path / "test.db"))
    from equinox.storage import get_db

    return get_db()


@pytest.fixture(autouse=True)
def isolated_gui_settings(tmp_path, monkeypatch):
    """Give every GUI test its own empty settings store. See issue #40.

    QSettings' native backend is the user's registry on Windows, so without
    this the suite reads and writes real user state: font size, theme, proxy
    config and saved layout are all rewritten by whatever a test asserted
    last. It also leaks between test *processes* -- the pre-push hook runs
    affected files as separate pytest runs -- which made the zoom tests
    depend on what an earlier run happened to leave behind.

    The root tests/conftest.py's `isolated_environment` does not cover this:
    it redirects EQUINOX_HOME, which the native QSettings backend ignores.

    Every settings read goes through ui_common.get_gui_settings(), so
    replacing its cached handle isolates the whole suite in one place.
    """
    import equinox.gui.ui_common as ui_common

    handle = QSettings(str(tmp_path / "gui-settings.ini"), QSettings.Format.IniFormat)
    monkeypatch.setattr(ui_common, "_settings_handle", handle)
    yield handle
    ui_common.reset_gui_settings_handle()
