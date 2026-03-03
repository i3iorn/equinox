"""Tests for request reordering and sorting.

Covers:
- sort_order column exists after migration
- list_requests respects sort_order
- reorder_requests bulk-updates sort_order
- sort_requests_alphabetically sorts A→Z within a group
- sort_requests_by_method sorts by method then name
- Sorting is scoped to a single collection+folder group
- Reorder via drag-drop places the dragged item before the target
"""

import pytest

from equinox.storage.database import Database
from equinox.storage.collections import CollectionManager
from equinox.storage.migrations import MigrationRunner
from equinox.core.request import Request


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
def col_id(mgr):
    return mgr.create_collection("Test Collection")


@pytest.fixture
def seeded_requests(mgr, col_id):
    """Create several requests with known names/methods, return list of (name, id)."""
    items = [
        ("Delete Users", "DELETE"),
        ("Create User", "POST"),
        ("Get Users", "GET"),
        ("Update User", "PUT"),
        ("Ping", "HEAD"),
    ]
    ids = {}
    for name, method in items:
        req = Request(method=method, url=f"https://api.example.com/{name.lower().replace(' ', '-')}", name=name)
        ids[name] = mgr.save_request(req, collection_id=col_id, name=name)
    return ids


# ── Migration ─────────────────────────────────────────────────────────────────

class TestSortOrderMigration:
    def test_sort_order_column_exists(self, db):
        rows = db.fetchall("PRAGMA table_info(requests)")
        col_names = [r["name"] for r in rows]
        assert "sort_order" in col_names

    def test_sort_order_defaults_to_zero(self, mgr, col_id):
        req = Request(method="GET", url="https://example.com", name="Test")
        req_id = mgr.save_request(req, collection_id=col_id, name="Test")
        row = mgr.db.fetchone("SELECT sort_order FROM requests WHERE id=?", (req_id,))
        assert row["sort_order"] == 0


# ── list_requests ordering ────────────────────────────────────────────────────

class TestListRequestsOrdering:
    def test_order_by_sort_order_then_name(self, mgr, col_id, seeded_requests):
        # Assign explicit sort order (reverse alphabetical)
        mgr.set_sort_order(seeded_requests["Ping"], 0)
        mgr.set_sort_order(seeded_requests["Update User"], 1)
        mgr.set_sort_order(seeded_requests["Get Users"], 2)
        mgr.set_sort_order(seeded_requests["Delete Users"], 3)
        mgr.set_sort_order(seeded_requests["Create User"], 4)

        rows = mgr.list_requests(col_id)
        names = [r["name"] for r in rows]
        assert names == ["Ping", "Update User", "Get Users", "Delete Users", "Create User"]

    def test_same_sort_order_falls_back_to_name(self, mgr, col_id, seeded_requests):
        # All have sort_order=0 (default) → fallback to name alphabetical
        rows = mgr.list_requests(col_id)
        names = [r["name"] for r in rows]
        assert names == sorted(names)


# ── reorder_requests ──────────────────────────────────────────────────────────

class TestReorderRequests:
    def test_reorder_bulk(self, mgr, col_id, seeded_requests):
        desired = ["Ping", "Get Users", "Create User", "Update User", "Delete Users"]
        ordered_ids = [seeded_requests[n] for n in desired]
        mgr.reorder_requests(ordered_ids)

        rows = mgr.list_requests(col_id)
        names = [r["name"] for r in rows]
        assert names == desired

    def test_reorder_single_move_to_front(self, mgr, col_id, seeded_requests):
        # First, establish a known order
        all_names = ["Create User", "Delete Users", "Get Users", "Ping", "Update User"]
        ordered_ids = [seeded_requests[n] for n in all_names]
        mgr.reorder_requests(ordered_ids)

        # Move "Update User" to front
        new_order = [seeded_requests["Update User"]] + [
            seeded_requests[n] for n in all_names if n != "Update User"
        ]
        mgr.reorder_requests(new_order)

        rows = mgr.list_requests(col_id)
        names = [r["name"] for r in rows]
        assert names[0] == "Update User"


# ── sort_requests_alphabetically ──────────────────────────────────────────────

class TestSortAlphabetically:
    def test_sorts_az(self, mgr, col_id, seeded_requests):
        # Scramble order first
        mgr.set_sort_order(seeded_requests["Ping"], 0)
        mgr.set_sort_order(seeded_requests["Delete Users"], 1)
        mgr.set_sort_order(seeded_requests["Update User"], 2)
        mgr.set_sort_order(seeded_requests["Get Users"], 3)
        mgr.set_sort_order(seeded_requests["Create User"], 4)

        mgr.sort_requests_alphabetically(col_id)

        rows = mgr.list_requests(col_id)
        names = [r["name"] for r in rows]
        assert names == sorted(names, key=str.lower)

    def test_scoped_to_folder(self, mgr, col_id, seeded_requests):
        # Move some requests to a folder
        mgr.move_request_to_folder(seeded_requests["Ping"], "Utils")
        mgr.move_request_to_folder(seeded_requests["Get Users"], "Utils")

        # Sort only the folder
        mgr.sort_requests_alphabetically(col_id, folder="Utils")

        # Root requests should be unaffected (still default order)
        root_rows = mgr._select_group(col_id, None)
        folder_rows = mgr._select_group(col_id, "Utils")

        folder_names = [r["name"] for r in folder_rows]
        assert folder_names == sorted(folder_names, key=str.lower)


# ── sort_requests_by_method ───────────────────────────────────────────────────

class TestSortByMethod:
    def test_sorts_by_method_then_name(self, mgr, col_id, seeded_requests):
        mgr.sort_requests_by_method(col_id)

        rows = mgr.list_requests(col_id)
        methods = [r["method"] for r in rows]
        # Expected method order: GET, POST, PUT, DELETE, HEAD
        expected_methods = ["GET", "POST", "PUT", "DELETE", "HEAD"]
        assert methods == expected_methods

    def test_same_method_sorted_by_name(self, mgr, col_id):
        # Create two GET requests
        for name in ["Zebra", "Alpha"]:
            req = Request(method="GET", url="https://example.com", name=name)
            mgr.save_request(req, collection_id=col_id, name=name)

        mgr.sort_requests_by_method(col_id)
        rows = mgr.list_requests(col_id)
        get_names = [r["name"] for r in rows if r["method"] == "GET"]
        assert get_names == sorted(get_names, key=str.lower)


# ── GUI reorder handler ──────────────────────────────────────────────────────

def _can_import_pyqt6() -> bool:
    try:
        import PyQt6  # noqa: F401
        return True
    except ImportError:
        return False


@pytest.mark.skipif(not _can_import_pyqt6(), reason="PyQt6 not available")
class TestGUIReorder:

    @pytest.fixture
    def qapp(self):
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is None:
            app = QApplication([])
        return app

    @pytest.fixture
    def panel(self, qapp, db):
        from equinox.gui.collections_panel_pkg import CollectionsPanel
        p = CollectionsPanel(db)
        yield p
        p.close()

    def test_reorder_via_drop(self, panel, mgr, col_id, seeded_requests):
        """Dropping request A onto request B should place A before B."""
        # Establish a known order
        order = ["Create User", "Delete Users", "Get Users", "Ping", "Update User"]
        ordered_ids = [seeded_requests[n] for n in order]
        mgr.reorder_requests(ordered_ids)
        panel.refresh()

        # Drag "Update User" onto "Delete Users" → should go before it
        panel._on_request_reorder(
            seeded_requests["Update User"],
            seeded_requests["Delete Users"],
        )

        rows = mgr.list_requests(col_id)
        names = [r["name"] for r in rows]
        assert names.index("Update User") < names.index("Delete Users")
        assert names.index("Update User") == 1  # right before Delete Users

    def test_sort_alpha_via_context(self, panel, mgr, col_id, seeded_requests):
        """_sort_group with mode='alpha' should sort A→Z."""
        panel.refresh()
        panel._sort_group(col_id, None, "alpha")

        rows = mgr.list_requests(col_id)
        names = [r["name"] for r in rows]
        assert names == sorted(names, key=str.lower)

    def test_sort_method_via_context(self, panel, mgr, col_id, seeded_requests):
        """_sort_group with mode='method' should sort by method."""
        panel.refresh()
        panel._sort_group(col_id, None, "method")

        rows = mgr.list_requests(col_id)
        methods = [r["method"] for r in rows]
        expected = ["GET", "POST", "PUT", "DELETE", "HEAD"]
        assert methods == expected

