"""Regression tests: collection/folder auth mutators must self-refresh.

Before this fix, _set_collection_auth/_clear_collection_auth/
_set_folder_auth/_clear_folder_auth only emitted collections_changed and
relied entirely on a window-level listener to call refresh() — so a
CollectionsPanel used without that external wiring (e.g. embedded
standalone, or in tests) would mutate storage but never repaint its own
tree, unlike every other mutator (create/rename/delete/move/sort), which
all call self.refresh() directly in addition to emitting the signal.
"""

import pytest

from equinox.auth import BearerAuth
from equinox.storage.collections import CollectionManager
from equinox.storage.database import Database
from equinox.storage.migrations import MigrationRunner


def _can_import_pyqt6() -> bool:
    try:
        import PyQt6  # noqa: F401

        return True
    except ImportError:
        return False


pytestmark = pytest.mark.skipif(not _can_import_pyqt6(), reason="PyQt6 not available")


@pytest.fixture(scope="session")
def qapp():
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def db(tmp_path):
    db_path = tmp_path / "test.db"
    database = Database(str(db_path))
    MigrationRunner(database).run()
    return database


@pytest.fixture
def mgr(db):
    return CollectionManager(db)


@pytest.fixture
def panel(qapp, db):
    from equinox.gui.collection_panel import CollectionsPanel

    # Deliberately not connected to collections_changed by anything (unlike
    # the real MainWindow) — refresh() must be self-sufficient.
    p = CollectionsPanel(db)
    yield p
    p.close()


def test_clear_collection_auth_refreshes_without_external_listener(panel, mgr, monkeypatch):
    col_id = mgr.create_collection("Alpha")
    mgr.set_collection_auth(col_id, BearerAuth(token="abc"))
    panel.refresh()

    calls = []
    monkeypatch.setattr(panel, "refresh", lambda: calls.append(1))

    panel._clear_collection_auth(col_id)

    assert calls, "refresh() must be called directly, not only via the signal"
    assert mgr.get_collection_auth(col_id) is None


def test_clear_folder_auth_refreshes_without_external_listener(panel, mgr, monkeypatch):
    col_id = mgr.create_collection("Alpha")
    mgr.create_folder(col_id, "Auth")
    mgr.set_folder_auth(col_id, "Auth", BearerAuth(token="abc"))
    panel.refresh()

    calls = []
    monkeypatch.setattr(panel, "refresh", lambda: calls.append(1))

    panel._clear_folder_auth(col_id, "Auth")

    assert calls, "refresh() must be called directly, not only via the signal"
    assert mgr.get_folder_auth(col_id, "Auth") is None
