"""Round-trip tests for encrypted auth persistence in storage layers."""

from __future__ import annotations

from pathlib import Path

from equinox.auth import BearerAuth
from equinox.core.request import Request
from equinox.storage import CollectionManager, Database


def _make_db(tmp_path: Path) -> Database:
    db_path = tmp_path / "auth_roundtrip.db"
    return Database(str(db_path))


def test_collection_auth_roundtrip_is_encrypted(tmp_path) -> None:
    db = _make_db(tmp_path)
    mgr = CollectionManager(db)
    collection_id = mgr.create_collection("Auth Test")

    mgr.set_collection_auth(collection_id, BearerAuth("top-secret-token"))
    auth = mgr.get_collection_auth(collection_id)
    assert isinstance(auth, BearerAuth)
    assert auth.token == "top-secret-token"

    row = db.fetchone("SELECT auth_data FROM collections WHERE id = ?", (collection_id,))
    assert row is not None
    assert str(row["auth_data"]).startswith("enc:")


def test_request_auth_roundtrip_after_update_request_auth(tmp_path) -> None:
    db = _make_db(tmp_path)
    mgr = CollectionManager(db)
    collection_id = mgr.create_collection("Request Auth")

    request_id = mgr.save_request(
        Request(method="GET", url="https://example.com", name="r1"),
        collection_id=collection_id,
    )

    mgr.update_request_auth(request_id, BearerAuth("req-token"))
    loaded = mgr.get_request(request_id)
    assert loaded is not None
    assert isinstance(loaded.auth, BearerAuth)
    assert loaded.auth.token == "req-token"

    row = db.fetchone("SELECT auth_data FROM requests WHERE id = ?", (request_id,))
    assert row is not None
    assert str(row["auth_data"]).startswith("enc:")


def test_folder_auth_is_used_for_effective_auth_resolution(tmp_path) -> None:
    db = _make_db(tmp_path)
    mgr = CollectionManager(db)
    collection_id = mgr.create_collection("Folder Auth")

    mgr.create_folder(collection_id, "team")
    mgr.set_folder_auth(collection_id, "team", BearerAuth("folder-token"))

    request_id = mgr.save_request(
        Request(
            method="GET",
            url="https://example.com/team",
            name="folder request",
            folder="team",
            collection_id=collection_id,
        )
    )
    request = mgr.get_request(request_id)
    assert request is not None

    effective_auth, source = mgr.resolve_effective_auth(request)
    assert isinstance(effective_auth, BearerAuth)
    assert effective_auth.token == "folder-token"
    assert source == "folder:team"
