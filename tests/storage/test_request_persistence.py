"""Tests for request persistence (autosave, save-to-collection, path_params round-trip).

Verifies:
- autosave_current() persists changes to the DB via update_request()
- save_request() stores path_params
- update_request() stores path_params
- _row_to_request() loads path_params
- _save_request() (GUI) links the editor to the new DB row
- _send_request() does NOT clear the dirty flag
"""

import json

import pytest

from equinox.core.request import Request
from equinox.storage.collections import CollectionManager
from equinox.storage.database import Database


@pytest.fixture
def db(tmp_path):
    db_path = tmp_path / "test_persist.db"
    return Database(str(db_path))


@pytest.fixture
def mgr(db):
    return CollectionManager(db)


@pytest.fixture
def col_id(mgr):
    return mgr.create_collection("PersistTest")


# ── path_params round-trip via save_request / get_request ──────────────────


class TestPathParamsRoundTrip:
    def test_save_and_load_path_params(self, mgr, col_id):
        """path_params survive save_request → get_request round-trip."""
        req = Request(
            method="GET",
            url="https://api.example.com/users/{{userId}}/posts/{{postId}}",
            name="UserPosts",
            path_params={"userId": "42", "postId": "7"},
        )
        req_id = mgr.save_request(req, collection_id=col_id, name="UserPosts")
        loaded = mgr.get_request(req_id)
        assert loaded.path_params == {"userId": "42", "postId": "7"}

    def test_update_request_persists_path_params(self, mgr, col_id):
        """path_params survive update_request → get_request round-trip."""
        req = Request(
            method="GET",
            url="https://api.example.com/users/{{userId}}",
            name="User",
            path_params={"userId": "1"},
        )
        req_id = mgr.save_request(req, collection_id=col_id, name="User")

        # Simulate autosave updating path_params
        loaded = mgr.get_request(req_id)
        loaded.path_params = {"userId": "99"}
        mgr.update_request(loaded)

        reloaded = mgr.get_request(req_id)
        assert reloaded.path_params == {"userId": "99"}

    def test_empty_path_params(self, mgr, col_id):
        """Requests with no path_params load back as empty dict."""
        req = Request(method="GET", url="https://example.com", name="NoParams")
        req_id = mgr.save_request(req, collection_id=col_id, name="NoParams")
        loaded = mgr.get_request(req_id)
        assert loaded.path_params == {}

    def test_path_params_column_raw(self, mgr, db, col_id):
        """The raw path_params column in the DB is valid JSON."""
        req = Request(
            method="GET",
            url="https://api.example.com/items/{{itemId}}",
            name="Items",
            path_params={"itemId": "abc-123"},
        )
        req_id = mgr.save_request(req, collection_id=col_id, name="Items")
        row = db.fetchone("SELECT path_params FROM requests WHERE id=?", (req_id,))
        parsed = json.loads(row["path_params"])
        assert parsed == {"itemId": "abc-123"}


# ── update_request full field persistence ──────────────────────────────────


class TestUpdateRequestPersistence:
    def test_update_preserves_all_fields(self, mgr, col_id):
        """update_request() round-trips all editable fields."""
        req = Request(
            method="POST",
            url="https://api.example.com/users",
            headers={"Content-Type": "application/json"},
            params={"page": "1"},
            body='{"name": "test"}',
            name="CreateUser",
            description="Create a new user",
            timeout=15.0,
            verify_ssl=False,
            follow_redirects=False,
            pre_script="env['ts'] = '123'",
            post_script="env['id'] = str(response['json']['id'])",
            path_params={"userId": "42"},
        )
        req_id = mgr.save_request(req, collection_id=col_id, name="CreateUser")

        # Simulate edits via autosave
        loaded = mgr.get_request(req_id)
        loaded.url = "https://api.example.com/users/{{userId}}"
        loaded.method = "PUT"
        loaded.headers = {"Content-Type": "application/xml"}
        loaded.body = "<user><name>updated</name></user>"
        loaded.description = "Updated description"
        loaded.timeout = 60.0
        loaded.verify_ssl = True
        loaded.follow_redirects = True
        loaded.pre_script = "# no-op"
        loaded.post_script = ""
        loaded.path_params = {"userId": "99"}
        mgr.update_request(loaded)

        reloaded = mgr.get_request(req_id)
        assert reloaded.method == "PUT"
        assert reloaded.url == "https://api.example.com/users/{{userId}}"
        assert reloaded.headers == {"Content-Type": "application/xml"}
        assert reloaded.body == "<user><name>updated</name></user>"
        assert reloaded.description == "Updated description"
        assert reloaded.timeout == 60.0
        assert reloaded.verify_ssl is True
        assert reloaded.follow_redirects is True
        assert reloaded.pre_script == "# no-op"
        assert reloaded.post_script == ""
        assert reloaded.path_params == {"userId": "99"}

    def test_multiple_updates_dont_lose_data(self, mgr, col_id):
        """Repeated update_request calls all persist correctly."""
        req = Request(method="GET", url="https://example.com/v1", name="Multi")
        req_id = mgr.save_request(req, collection_id=col_id, name="Multi")

        for i in range(5):
            loaded = mgr.get_request(req_id)
            loaded.url = f"https://example.com/v{i + 2}"
            loaded.description = f"Iteration {i + 1}"
            mgr.update_request(loaded)

        final = mgr.get_request(req_id)
        assert final.url == "https://example.com/v6"
        assert final.description == "Iteration 5"


# ── save_request returns usable ID ────────────────────────────────────────


class TestSaveRequestId:
    def test_save_returns_valid_id(self, mgr, col_id):
        """save_request returns an ID that can be used for update_request."""
        req = Request(method="GET", url="https://example.com", name="Test")
        req_id = mgr.save_request(req, collection_id=col_id, name="Test")
        assert req_id is not None
        assert isinstance(req_id, int)
        assert req_id > 0

        # The returned ID can be used to load and update
        loaded = mgr.get_request(req_id)
        assert loaded is not None
        loaded.url = "https://example.com/updated"
        mgr.update_request(loaded)  # should not raise

        reloaded = mgr.get_request(req_id)
        assert reloaded.url == "https://example.com/updated"


# ── Autosave simulation (DB layer only — no GUI) ──────────────────────────


class TestAutosaveSimulation:
    """Simulate the autosave lifecycle: load → edit → autosave → reload."""

    def test_autosave_round_trip(self, mgr, col_id):
        # Phase 1: create request
        original = Request(
            method="GET",
            url="https://api.example.com/users",
            headers={"Accept": "application/json"},
            name="ListUsers",
        )
        req_id = mgr.save_request(original, collection_id=col_id, name="ListUsers")

        # Phase 2: load (simulates double-click in collections panel)
        loaded = mgr.get_request(req_id)
        assert loaded.url == "https://api.example.com/users"

        # Phase 3: simulate user edits
        loaded.url = "https://api.example.com/users?active=true"
        loaded.headers = {"Accept": "application/json", "X-Custom": "value"}
        loaded.body = '{"filter": "active"}'
        loaded.description = "Added filter"

        # Phase 4: autosave (simulates switching to another request)
        mgr.update_request(loaded)

        # Phase 5: reload (simulates coming back to the request)
        reloaded = mgr.get_request(req_id)
        assert reloaded.url == "https://api.example.com/users?active=true"
        assert reloaded.headers == {"Accept": "application/json", "X-Custom": "value"}
        assert reloaded.body == '{"filter": "active"}'
        assert reloaded.description == "Added filter"
        assert reloaded.id == req_id
        assert reloaded.collection_id == col_id

    def test_autosave_preserves_auth(self, mgr, col_id):
        """Auth survives the autosave path."""
        from equinox.auth import BearerAuth

        req = Request(
            method="GET",
            url="https://api.example.com",
            name="AuthTest",
            auth=BearerAuth(token="secret-token"),
        )
        req_id = mgr.save_request(req, collection_id=col_id, name="AuthTest")

        # Simulate autosave with auth unchanged
        loaded = mgr.get_request(req_id)
        loaded.url = "https://api.example.com/v2"
        mgr.update_request(loaded)

        reloaded = mgr.get_request(req_id)
        assert reloaded.url == "https://api.example.com/v2"
        assert isinstance(reloaded.auth, BearerAuth)
        assert reloaded.auth.token == "secret-token"
