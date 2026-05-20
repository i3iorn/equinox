"""Tests for the collections panel filter + expansion state interaction.

Verifies that:
- The filter text is preserved after refresh() (e.g. after a drag-drop move)
- Filtered visibility is correct after refresh
- Expansion state is saved when filter is applied and restored when cleared
- Manual expand/collapse while filtered updates the saved state
"""

import pytest

from equinox.core.request import Request
from equinox.storage.collections import CollectionManager
from equinox.storage.database import Database
from equinox.storage.migrations import MigrationRunner


def _can_import_pyqt6() -> bool:
    try:
        import PyQt6  # noqa: F401

        return True
    except ImportError:
        return False


# ── PyQt6 availability gate ──────────────────────────────────────────────────
pytestmark = pytest.mark.skipif(
    not _can_import_pyqt6(),
    reason="PyQt6 not available",
)


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def qapp():
    """Create a single QApplication for the whole test session."""
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

    p = CollectionsPanel(db)
    yield p
    p.close()


@pytest.fixture
def seeded(mgr):
    """Seed two collections with requests (some in folders) and return metadata."""
    col_a = mgr.create_collection("Alpha API")
    col_b = mgr.create_collection("Beta API")
    mgr.create_folder(col_a, "Auth")
    ids = {}
    for name, method, col, folder in [
        ("Get Users", "GET", col_a, None),
        ("Create User", "POST", col_a, "Auth"),
        ("Delete User", "DELETE", col_a, None),
        ("Get Items", "GET", col_b, None),
    ]:
        req = Request(
            method=method,
            url=f"https://example.com/{name.lower().replace(' ', '-')}",
            name=name,
            folder=folder,
        )
        ids[name] = mgr.save_request(req, collection_id=col, name=name)
    return {"col_a": col_a, "col_b": col_b, "requests": ids}


# ── Helpers ───────────────────────────────────────────────────────────────────


def _collect_request_visibility(panel):
    """Walk tree and return (visible_names, hidden_names) lists for requests."""
    from PyQt6.QtCore import Qt

    visible, hidden = [], []

    def _walk(parent):
        for j in range(parent.childCount()):
            child = parent.child(j)
            data = child.data(0, Qt.ItemDataRole.UserRole) or {}
            if data.get("type") == "folder":
                _walk(child)
            elif data.get("type") == "request":
                (hidden if child.isHidden() else visible).append(child.text(0))

    for i in range(panel.tree.topLevelItemCount()):
        col_item = panel.tree.topLevelItem(i)
        _walk(col_item)
    return visible, hidden


def _get_col_item(panel, col_id):
    """Return the top-level QTreeWidgetItem for the given collection ID."""
    from PyQt6.QtCore import Qt

    for i in range(panel.tree.topLevelItemCount()):
        item = panel.tree.topLevelItem(i)
        data = item.data(0, Qt.ItemDataRole.UserRole) or {}
        if data.get("id") == col_id:
            return item
    return None


def _get_folder_item(panel, col_id, folder_path):
    """Return the QTreeWidgetItem for a folder inside a collection."""
    from PyQt6.QtCore import Qt

    def _find(parent, target_path):
        for j in range(parent.childCount()):
            child = parent.child(j)
            data = child.data(0, Qt.ItemDataRole.UserRole) or {}
            if data.get("type") == "folder" and data.get("path") == target_path:
                return child
            found = _find(child, target_path)
            if found:
                return found
        return None

    col_item = _get_col_item(panel, col_id)
    if col_item is None:
        return None
    return _find(col_item, folder_path)


# ── Tests: filter text preserved ──────────────────────────────────────────────


class TestFilterPreservedOnRefresh:
    """The active filter must survive a tree refresh (triggered by drag-drop, etc.)."""

    def test_filter_text_preserved_after_refresh(self, panel, seeded):
        panel.refresh()
        panel._filter_input.setText("User")
        panel.refresh()
        assert panel._filter_input.text() == "User"

    def test_filter_hides_non_matching_after_refresh(self, panel, seeded):
        panel.refresh()
        panel._filter_input.setText("User")
        panel.refresh()

        visible, hidden = _collect_request_visibility(panel)
        assert any("User" in n for n in visible)
        assert any("Items" in n for n in hidden)

    def test_clearing_filter_shows_all_after_refresh(self, panel, seeded):
        panel.refresh()
        panel._filter_input.setText("User")
        panel.refresh()
        panel._filter_input.setText("")

        _, hidden = _collect_request_visibility(panel)
        assert len(hidden) == 0

    def test_drop_handler_preserves_filter(self, panel, seeded, mgr):
        panel.refresh()
        panel._filter_input.setText("User")
        req_id = seeded["requests"]["Get Users"]
        col_b = seeded["col_b"]
        panel._on_request_dropped(req_id, col_b, None)

        assert panel._filter_input.text() == "User"
        assert mgr.get_request(req_id).collection_id == col_b

    def test_drop_same_location_preserves_filter(self, panel, seeded):
        panel.refresh()
        panel._filter_input.setText("Items")
        req_id = seeded["requests"]["Get Items"]
        col_b = seeded["col_b"]
        panel._on_request_dropped(req_id, col_b, None)
        assert panel._filter_input.text() == "Items"


# ── Tests: expansion restored after filter cleared ────────────────────────────


class TestExpansionRestoredAfterFilter:
    """When the filter is cleared, collections/folders must return to their
    pre-filter expanded/collapsed state."""

    def test_collapsed_collection_stays_collapsed(self, panel, seeded):
        """A collection that was collapsed before filtering must be collapsed
        again after the filter is cleared."""
        panel.refresh()
        col_a_item = _get_col_item(panel, seeded["col_a"])
        col_b_item = _get_col_item(panel, seeded["col_b"])

        # Collapse both
        col_a_item.setExpanded(False)
        col_b_item.setExpanded(False)

        # Apply filter → collections expand to reveal matches
        panel._filter_input.setText("User")
        assert col_a_item.isExpanded(), "Filter should expand matching collection"

        # Clear filter → should restore collapsed state
        panel._filter_input.setText("")
        assert not col_a_item.isExpanded(), "Collection A should be collapsed after clearing filter"
        assert not col_b_item.isExpanded(), "Collection B should be collapsed after clearing filter"

    def test_expanded_collection_stays_expanded(self, panel, seeded):
        """A collection that was expanded before filtering must stay expanded
        after the filter is cleared."""
        panel.refresh()
        col_a_item = _get_col_item(panel, seeded["col_a"])

        col_a_item.setExpanded(True)

        panel._filter_input.setText("User")
        panel._filter_input.setText("")

        assert col_a_item.isExpanded(), "Collection A should still be expanded"

    def test_mixed_expansion_restored(self, panel, seeded):
        """One expanded and one collapsed collection — each restored correctly."""
        panel.refresh()
        col_a_item = _get_col_item(panel, seeded["col_a"])
        col_b_item = _get_col_item(panel, seeded["col_b"])

        col_a_item.setExpanded(True)
        col_b_item.setExpanded(False)

        panel._filter_input.setText("Get")
        # Both should be expanded during filter (both have "Get" requests)
        assert col_a_item.isExpanded()
        assert col_b_item.isExpanded()

        panel._filter_input.setText("")
        assert col_a_item.isExpanded(), "Collection A was expanded before filter"
        assert not col_b_item.isExpanded(), "Collection B was collapsed before filter"

    def test_folder_expansion_restored(self, panel, seeded):
        """Folder expansion state is restored after filter is cleared."""
        panel.refresh()
        col_a_item = _get_col_item(panel, seeded["col_a"])
        col_a_item.setExpanded(True)

        folder_item = _get_folder_item(panel, seeded["col_a"], "Auth")
        assert folder_item is not None, "Auth folder should exist"

        # Collapse the folder
        folder_item.setExpanded(False)

        # Filter → folder expands to show match
        panel._filter_input.setText("Create User")
        assert folder_item.isExpanded(), "Folder should expand during filter"

        # Clear → folder goes back to collapsed
        panel._filter_input.setText("")
        folder_item = _get_folder_item(panel, seeded["col_a"], "Auth")
        assert not folder_item.isExpanded(), "Auth folder should be collapsed after clearing filter"

    def test_expansion_restored_after_refresh_then_clear(self, panel, seeded):
        """Expansion state survives a refresh() that happens while filtered."""
        panel.refresh()
        col_a_item = _get_col_item(panel, seeded["col_a"])
        col_b_item = _get_col_item(panel, seeded["col_b"])

        col_a_item.setExpanded(True)
        col_b_item.setExpanded(False)

        panel._filter_input.setText("User")
        # Simulate a refresh during filter (e.g. drag-drop)
        panel.refresh()

        # Clear filter
        panel._filter_input.setText("")
        col_a_item = _get_col_item(panel, seeded["col_a"])
        col_b_item = _get_col_item(panel, seeded["col_b"])
        assert col_a_item.isExpanded(), "Collection A should be expanded"
        assert not col_b_item.isExpanded(), "Collection B should be collapsed"


# ── Tests: manual expand/collapse while filtered updates saved state ──────────


class TestManualExpandCollapseWhileFiltered:
    """If the user expands or collapses a collection/folder while a filter is
    active, that change must be reflected when the filter is cleared."""

    def test_collapse_collection_while_filtered(self, panel, seeded):
        """Collapsing a collection while filtered → stays collapsed after clear."""
        panel.refresh()
        col_a_item = _get_col_item(panel, seeded["col_a"])
        col_a_item.setExpanded(True)

        panel._filter_input.setText("User")
        assert col_a_item.isExpanded()

        # User manually collapses while filtered
        col_a_item.setExpanded(False)

        # Clear filter
        panel._filter_input.setText("")
        col_a_item = _get_col_item(panel, seeded["col_a"])
        assert (
            not col_a_item.isExpanded()
        ), "Collection A was collapsed while filtered, should stay collapsed"

    def test_expand_collection_while_filtered(self, panel, seeded):
        """Collapsing then re-expanding a collection while filtered → stays expanded after clear."""
        panel.refresh()
        col_b_item = _get_col_item(panel, seeded["col_b"])
        col_b_item.setExpanded(False)

        panel._filter_input.setText("Items")
        # Filter force-expands it; user collapses then re-expands (updating saved state)
        col_b_item.setExpanded(False)
        col_b_item.setExpanded(True)

        panel._filter_input.setText("")
        col_b_item = _get_col_item(panel, seeded["col_b"])
        assert (
            col_b_item.isExpanded()
        ), "Collection B was re-expanded while filtered, should stay expanded"

    def test_collapse_folder_while_filtered(self, panel, seeded):
        """Collapsing a folder while filtered → stays collapsed after clear."""
        panel.refresh()
        col_a_item = _get_col_item(panel, seeded["col_a"])
        col_a_item.setExpanded(True)
        folder_item = _get_folder_item(panel, seeded["col_a"], "Auth")
        folder_item.setExpanded(True)

        panel._filter_input.setText("Create User")
        folder_item = _get_folder_item(panel, seeded["col_a"], "Auth")
        assert folder_item.isExpanded()

        # User manually collapses
        folder_item.setExpanded(False)

        panel._filter_input.setText("")
        folder_item = _get_folder_item(panel, seeded["col_a"], "Auth")
        assert (
            not folder_item.isExpanded()
        ), "Auth folder was collapsed while filtered, should stay collapsed"

    def test_no_snapshot_without_filter(self, panel, seeded):
        """_pre_filter_expansion should be None when no filter is active."""
        panel.refresh()
        assert panel._pre_filter_expansion is None
        col_a_item = _get_col_item(panel, seeded["col_a"])
        col_a_item.setExpanded(True)
        col_a_item.setExpanded(False)
        assert panel._pre_filter_expansion is None, "Should not snapshot when no filter is active"
