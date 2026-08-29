"""Shared fixtures for the GUI test suite.

The `db` fixture below was previously re-typed, byte-identical, in nine
separate GUI test modules. It lives here so a change to how tests get a
database is made once.

The QApplication itself is built by the root tests/conftest.py; see
`gui_helpers` for the event-loop pump that goes with it.
"""

from __future__ import annotations

import pytest


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """Return a Database backed by a throwaway per-test SQLite file."""
    monkeypatch.setenv("EQUINOX_DB_PATH", str(tmp_path / "test.db"))
    from equinox.storage import get_db

    return get_db()
