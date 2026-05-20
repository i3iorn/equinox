"""Tests for moving requests between folders and collections.

Covers:
- move_request_to_folder within a collection
- move_request_to_collection (cross-collection move)
- move_request_to_collection with a target folder
- Error cases: non-existent request, non-existent target collection
"""

import pytest

from equinox.core.exceptions import StorageError
from equinox.core.request import Request
from equinox.storage.collections import CollectionManager
from equinox.storage.database import Database
from equinox.storage.migrations import MigrationRunner


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
def two_collections(mgr):
    """Create two collections and return their IDs."""
    col_a = mgr.create_collection("Collection A")
    col_b = mgr.create_collection("Collection B")
    return col_a, col_b


@pytest.fixture
def request_in_a(mgr, two_collections):
    """Create a request in Collection A and return its ID."""
    col_a, _ = two_collections
    req = Request(method="GET", url="https://example.com", name="Test Request")
    return mgr.save_request(req, collection_id=col_a, name="Test Request")


# ── move_request_to_folder ────────────────────────────────────────────────────


class TestMoveToFolder:
    def test_move_to_folder(self, mgr, two_collections, request_in_a):
        col_a, _ = two_collections
        mgr.move_request_to_folder(request_in_a, "Auth")
        req = mgr.get_request(request_in_a)
        assert req.folder == "Auth"

    def test_move_to_root(self, mgr, two_collections, request_in_a):
        mgr.move_request_to_folder(request_in_a, "Auth")
        mgr.move_request_to_folder(request_in_a, None)
        req = mgr.get_request(request_in_a)
        assert req.folder is None

    def test_move_to_nested_folder(self, mgr, two_collections, request_in_a):
        mgr.move_request_to_folder(request_in_a, "Auth/OAuth")
        req = mgr.get_request(request_in_a)
        assert req.folder == "Auth/OAuth"


# ── move_request_to_collection ────────────────────────────────────────────────


class TestMoveToCollection:
    def test_move_between_collections(self, mgr, two_collections, request_in_a):
        col_a, col_b = two_collections
        mgr.move_request_to_collection(request_in_a, col_b)
        req = mgr.get_request(request_in_a)
        assert req.collection_id == col_b
        assert req.folder is None

    def test_move_to_collection_with_folder(self, mgr, two_collections, request_in_a):
        col_a, col_b = two_collections
        mgr.move_request_to_collection(request_in_a, col_b, folder="Auth")
        req = mgr.get_request(request_in_a)
        assert req.collection_id == col_b
        assert req.folder == "Auth"

    def test_move_preserves_request_data(self, mgr, two_collections, request_in_a):
        col_a, col_b = two_collections
        req_before = mgr.get_request(request_in_a)
        mgr.move_request_to_collection(request_in_a, col_b)
        req_after = mgr.get_request(request_in_a)
        assert req_after.name == req_before.name
        assert req_after.method == req_before.method
        assert req_after.url == req_before.url

    def test_source_collection_no_longer_has_request(self, mgr, two_collections, request_in_a):
        col_a, col_b = two_collections
        mgr.move_request_to_collection(request_in_a, col_b)
        requests_in_a = mgr.list_requests(col_a)
        assert all(r["id"] != request_in_a for r in requests_in_a)

    def test_target_collection_has_request(self, mgr, two_collections, request_in_a):
        col_a, col_b = two_collections
        mgr.move_request_to_collection(request_in_a, col_b)
        requests_in_b = mgr.list_requests(col_b)
        assert any(r["id"] == request_in_a for r in requests_in_b)

    def test_nonexistent_request_raises(self, mgr, two_collections):
        _, col_b = two_collections
        with pytest.raises(StorageError, match="not found"):
            mgr.move_request_to_collection(99999, col_b)

    def test_nonexistent_collection_raises(self, mgr, two_collections, request_in_a):
        with pytest.raises(StorageError, match="does not exist"):
            mgr.move_request_to_collection(request_in_a, 99999)
