"""Tests for explicit folder management in collections.

Covers:
- create_folder / list_folders happy path
- Duplicate folder is idempotent (INSERT OR IGNORE)
- Invalid path validation
- list_folders returns sorted results
- rename_folder keeps collection_folders in sync
- delete_folder removes collection_folders records (including for empty folders)
- delete_folder sub-folder cleanup
"""

import pytest

from equinox.core.exceptions import StorageError, ValidationError
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
def col_id(mgr):
    return mgr.create_collection("Test Collection")


# ── create_folder ─────────────────────────────────────────────────────────────


class TestCreateFolder:
    def test_creates_folder(self, mgr, col_id):
        mgr.create_folder(col_id, "Auth")
        assert "Auth" in mgr.list_folders(col_id)

    def test_idempotent_on_duplicate(self, mgr, col_id):
        mgr.create_folder(col_id, "Auth")
        mgr.create_folder(col_id, "Auth")  # should not raise
        assert mgr.list_folders(col_id).count("Auth") == 1

    def test_nested_path(self, mgr, col_id):
        mgr.create_folder(col_id, "Auth/OAuth")
        folders = mgr.list_folders(col_id)
        assert "Auth/OAuth" in folders

    def test_multiple_folders_sorted(self, mgr, col_id):
        mgr.create_folder(col_id, "Zeta")
        mgr.create_folder(col_id, "Alpha")
        mgr.create_folder(col_id, "Beta")
        folders = mgr.list_folders(col_id)
        assert folders == sorted(folders)

    def test_invalid_empty_path(self, mgr, col_id):
        with pytest.raises(ValidationError):
            mgr.create_folder(col_id, "")

    def test_invalid_leading_slash(self, mgr, col_id):
        with pytest.raises(ValidationError):
            mgr.create_folder(col_id, "/Auth")

    def test_invalid_trailing_slash(self, mgr, col_id):
        with pytest.raises(ValidationError):
            mgr.create_folder(col_id, "Auth/")

    def test_invalid_double_slash(self, mgr, col_id):
        with pytest.raises(ValidationError):
            mgr.create_folder(col_id, "Auth//OAuth")

    def test_invalid_collection(self, mgr):
        with pytest.raises((ValidationError, StorageError)):
            mgr.create_folder(99999, "Auth")

    def test_invalid_collection_id_type(self, mgr):
        with pytest.raises(ValidationError):
            mgr.create_folder("bad", "Auth")


# ── list_folders ──────────────────────────────────────────────────────────────


class TestListFolders:
    def test_empty_by_default(self, mgr, col_id):
        assert mgr.list_folders(col_id) == []

    def test_returns_only_own_collection_folders(self, mgr, col_id):
        other_id = mgr.create_collection("Other")
        mgr.create_folder(col_id, "Mine")
        mgr.create_folder(other_id, "Theirs")
        assert mgr.list_folders(col_id) == ["Mine"]
        assert mgr.list_folders(other_id) == ["Theirs"]


# ── rename_folder ─────────────────────────────────────────────────────────────


class TestRenameFolderSync:
    def test_renames_explicit_folder_record(self, mgr, col_id):
        mgr.create_folder(col_id, "Auth")
        mgr.rename_folder(col_id, "Auth", "Authentication")
        folders = mgr.list_folders(col_id)
        assert "Authentication" in folders
        assert "Auth" not in folders

    def test_renames_nested_subfolder_records(self, mgr, col_id):
        mgr.create_folder(col_id, "Auth")
        mgr.create_folder(col_id, "Auth/OAuth")
        mgr.create_folder(col_id, "Auth/OAuth/Implicit")
        mgr.rename_folder(col_id, "Auth", "Authentication")
        folders = mgr.list_folders(col_id)
        assert "Authentication" in folders
        assert "Authentication/OAuth" in folders
        assert "Authentication/OAuth/Implicit" in folders
        # Old paths gone
        assert "Auth" not in folders
        assert "Auth/OAuth" not in folders

    def test_renames_requests_and_folder_records(self, mgr, col_id):
        """rename_folder should update both requests.folder and collection_folders.path."""
        from equinox.core.request import Request

        req = Request(
            method="GET",
            url="https://example.com",
            name="My Req",
            collection_id=col_id,
            folder="Auth",
        )
        mgr.save_request(req, collection_id=col_id)
        mgr.create_folder(col_id, "Auth")

        mgr.rename_folder(col_id, "Auth", "Authentication")

        # Request moved
        reqs = mgr.list_requests(col_id)
        assert reqs[0]["folder"] == "Authentication"
        # Folder record renamed
        assert "Authentication" in mgr.list_folders(col_id)
        assert "Auth" not in mgr.list_folders(col_id)


# ── delete_folder ─────────────────────────────────────────────────────────────


class TestDeleteFolderSync:
    def test_deletes_empty_folder_record(self, mgr, col_id):
        mgr.create_folder(col_id, "Empty")
        mgr.delete_folder(col_id, "Empty")
        assert "Empty" not in mgr.list_folders(col_id)

    def test_deletes_nested_folder_records(self, mgr, col_id):
        mgr.create_folder(col_id, "Auth")
        mgr.create_folder(col_id, "Auth/OAuth")
        mgr.delete_folder(col_id, "Auth")
        assert "Auth" not in mgr.list_folders(col_id)
        assert "Auth/OAuth" not in mgr.list_folders(col_id)

    def test_move_to_root_clears_request_folder(self, mgr, col_id):
        from equinox.core.request import Request

        req = Request(
            method="GET",
            url="https://example.com",
            name="R",
            collection_id=col_id,
            folder="Auth",
        )
        mgr.save_request(req, collection_id=col_id)
        mgr.create_folder(col_id, "Auth")

        mgr.delete_folder(col_id, "Auth", move_to_root=True)

        reqs = mgr.list_requests(col_id)
        assert reqs[0]["folder"] is None
        assert "Auth" not in mgr.list_folders(col_id)

    def test_delete_requests_mode(self, mgr, col_id):
        from equinox.core.request import Request

        req = Request(
            method="GET",
            url="https://example.com",
            name="R",
            collection_id=col_id,
            folder="Auth",
        )
        mgr.save_request(req, collection_id=col_id)
        mgr.create_folder(col_id, "Auth")

        mgr.delete_folder(col_id, "Auth", move_to_root=False)

        assert mgr.list_requests(col_id) == []
        assert "Auth" not in mgr.list_folders(col_id)

    def test_delete_empty_folder_no_error(self, mgr, col_id):
        """Deleting an empty folder (no requests) must not raise and must remove record."""
        mgr.create_folder(col_id, "Empty")
        mgr.delete_folder(col_id, "Empty")  # previously a no-op; now cleans up
        assert mgr.list_folders(col_id) == []
