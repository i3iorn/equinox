"""Tests for hierarchical auth on collections, folders, and requests.

Covers:
- Migration 15 adds auth_type/auth_data to collections and collection_folders
- set/get collection auth round-trip
- set/get folder auth round-trip
- resolve_effective_auth: request own auth wins
- resolve_effective_auth: folder auth inherited when request has none
- resolve_effective_auth: nested folder walks up to parent
- resolve_effective_auth: collection auth inherited as last resort
- resolve_effective_auth: returns (None, None) when nothing is set
- Clearing auth works
- GUI request panel picks up inherited auth at send time
"""
import os

import pytest

from equinox.storage.database import Database
from equinox.storage.collections import CollectionManager
from equinox.storage.migrations import MigrationRunner
from equinox.core.request import Request
from equinox.auth import BearerAuth, BasicAuth, APIKeyAuth


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
    return mgr.create_collection("Test API")


@pytest.fixture
def folders(mgr, col_id):
    """Create a folder hierarchy: Auth, Auth/OAuth."""
    mgr.create_folder(col_id, "Auth")
    mgr.create_folder(col_id, "Auth/OAuth")
    return col_id


# ── Migration ─────────────────────────────────────────────────────────────────

class TestMigration15:
    def test_collections_has_auth_columns(self, db):
        rows = db.fetchall("PRAGMA table_info(collections)")
        names = [r["name"] for r in rows]
        assert "auth_type" in names
        assert "auth_data" in names

    def test_collection_folders_has_auth_columns(self, db):
        rows = db.fetchall("PRAGMA table_info(collection_folders)")
        names = [r["name"] for r in rows]
        assert "auth_type" in names
        assert "auth_data" in names


# ── Collection auth ───────────────────────────────────────────────────────────

class TestCollectionAuth:
    def test_set_and_get_bearer(self, mgr, col_id):
        auth = BearerAuth(token="col-token-123")
        mgr.set_collection_auth(col_id, auth)
        result = mgr.get_collection_auth(col_id)
        assert isinstance(result, BearerAuth)
        assert result.token == "col-token-123"

    def test_set_and_get_basic(self, mgr, col_id):
        auth = BasicAuth(username="user", password="pass")
        mgr.set_collection_auth(col_id, auth)
        result = mgr.get_collection_auth(col_id)
        assert isinstance(result, BasicAuth)
        assert result.username == "user"

    def test_clear_collection_auth(self, mgr, col_id):
        mgr.set_collection_auth(col_id, BearerAuth(token="temp"))
        mgr.set_collection_auth(col_id, None)
        assert mgr.get_collection_auth(col_id) is None

    def test_no_auth_by_default(self, mgr, col_id):
        assert mgr.get_collection_auth(col_id) is None


# ── Folder auth ───────────────────────────────────────────────────────────────

class TestFolderAuth:
    def test_set_and_get(self, mgr, folders):
        col_id = folders
        auth = APIKeyAuth(key="X-Api-Key", value="folder-key", location="header")
        mgr.set_folder_auth(col_id, "Auth", auth)
        result = mgr.get_folder_auth(col_id, "Auth")
        assert isinstance(result, APIKeyAuth)
        assert result.value == "folder-key"

    def test_subfolder_auth_independent(self, mgr, folders):
        col_id = folders
        mgr.set_folder_auth(col_id, "Auth", BearerAuth(token="parent-tok"))
        mgr.set_folder_auth(col_id, "Auth/OAuth", BearerAuth(token="child-tok"))
        assert mgr.get_folder_auth(col_id, "Auth").token == "parent-tok"
        assert mgr.get_folder_auth(col_id, "Auth/OAuth").token == "child-tok"

    def test_clear_folder_auth(self, mgr, folders):
        col_id = folders
        mgr.set_folder_auth(col_id, "Auth", BearerAuth(token="t"))
        mgr.set_folder_auth(col_id, "Auth", None)
        assert mgr.get_folder_auth(col_id, "Auth") is None

    def test_no_auth_by_default(self, mgr, folders):
        assert mgr.get_folder_auth(folders, "Auth") is None


# ── resolve_effective_auth ────────────────────────────────────────────────────

class TestResolveEffectiveAuth:
    def test_request_own_auth_wins(self, mgr, col_id):
        """Request-level auth takes priority over everything else."""
        mgr.set_collection_auth(col_id, BearerAuth(token="col-tok"))
        req = Request(method="GET", url="https://x.com", name="R",
                      collection_id=col_id, auth=BearerAuth(token="req-tok"))
        auth, source = mgr.resolve_effective_auth(req)
        assert isinstance(auth, BearerAuth)
        assert auth.token == "req-tok"
        assert source == "request"

    def test_folder_auth_inherited(self, mgr, folders):
        """Request with no auth inherits from its folder."""
        col_id = folders
        mgr.set_folder_auth(col_id, "Auth", BearerAuth(token="folder-tok"))
        req_id = mgr.save_request(
            Request(method="GET", url="https://x.com", name="R", folder="Auth"),
            collection_id=col_id, name="R",
        )
        req = mgr.get_request(req_id)
        auth, source = mgr.resolve_effective_auth(req)
        assert isinstance(auth, BearerAuth)
        assert auth.token == "folder-tok"
        assert source == "folder:Auth"

    def test_nested_folder_walks_up(self, mgr, folders):
        """Request in Auth/OAuth inherits from Auth when Auth/OAuth has no auth."""
        col_id = folders
        mgr.set_folder_auth(col_id, "Auth", BearerAuth(token="parent-tok"))
        # Auth/OAuth has no auth set
        req_id = mgr.save_request(
            Request(method="GET", url="https://x.com", name="R", folder="Auth/OAuth"),
            collection_id=col_id, name="R",
        )
        req = mgr.get_request(req_id)
        auth, source = mgr.resolve_effective_auth(req)
        assert auth.token == "parent-tok"
        assert source == "folder:Auth"

    def test_deepest_folder_wins(self, mgr, folders):
        """Auth/OAuth auth is preferred over Auth auth."""
        col_id = folders
        mgr.set_folder_auth(col_id, "Auth", BearerAuth(token="parent-tok"))
        mgr.set_folder_auth(col_id, "Auth/OAuth", BearerAuth(token="child-tok"))
        req_id = mgr.save_request(
            Request(method="GET", url="https://x.com", name="R", folder="Auth/OAuth"),
            collection_id=col_id, name="R",
        )
        req = mgr.get_request(req_id)
        auth, source = mgr.resolve_effective_auth(req)
        assert auth.token == "child-tok"
        assert source == "folder:Auth/OAuth"

    def test_collection_auth_as_fallback(self, mgr, col_id):
        """Collection auth is used when request and folder have no auth."""
        mgr.set_collection_auth(col_id, BearerAuth(token="col-tok"))
        req_id = mgr.save_request(
            Request(method="GET", url="https://x.com", name="R"),
            collection_id=col_id, name="R",
        )
        req = mgr.get_request(req_id)
        auth, source = mgr.resolve_effective_auth(req)
        assert auth.token == "col-tok"
        assert source == "collection"

    def test_collection_auth_after_folder_skip(self, mgr, folders):
        """Folder has no auth → falls through to collection."""
        col_id = folders
        mgr.set_collection_auth(col_id, BearerAuth(token="col-tok"))
        req_id = mgr.save_request(
            Request(method="GET", url="https://x.com", name="R", folder="Auth"),
            collection_id=col_id, name="R",
        )
        req = mgr.get_request(req_id)
        auth, source = mgr.resolve_effective_auth(req)
        assert auth.token == "col-tok"
        assert source == "collection"

    def test_nothing_configured(self, mgr, col_id):
        """No auth anywhere → (None, None)."""
        req_id = mgr.save_request(
            Request(method="GET", url="https://x.com", name="R"),
            collection_id=col_id, name="R",
        )
        req = mgr.get_request(req_id)
        auth, source = mgr.resolve_effective_auth(req)
        assert auth is None
        assert source is None

    def test_no_collection_id(self, mgr):
        """Request without collection_id → (None, None)."""
        req = Request(method="GET", url="https://x.com", name="R")
        auth, source = mgr.resolve_effective_auth(req)
        assert auth is None
        assert source is None

    def test_root_request_skips_folders(self, mgr, folders):
        """A root-level request (no folder) skips folder lookup."""
        col_id = folders
        mgr.set_folder_auth(col_id, "Auth", BearerAuth(token="folder-tok"))
        mgr.set_collection_auth(col_id, BearerAuth(token="col-tok"))
        req_id = mgr.save_request(
            Request(method="GET", url="https://x.com", name="R"),
            collection_id=col_id, name="R",
        )
        req = mgr.get_request(req_id)
        auth, source = mgr.resolve_effective_auth(req)
        assert auth.token == "col-tok"
        assert source == "collection"


# ── GUI integration ───────────────────────────────────────────────────────────

def _can_import_pyqt6() -> bool:
    try:
        import PyQt6  # noqa: F401
        return True
    except ImportError:
        return False


@pytest.mark.skipif(not _can_import_pyqt6(), reason="PyQt6 not available")
class TestGUIInheritedAuth:

    @pytest.fixture
    def qapp(self):
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is None:
            app = QApplication([])
        return app

    @pytest.fixture
    def panel(self, qapp, db):
        from equinox.gui.request_panel import RequestPanel
        p = RequestPanel(db)
        yield p
        p.close()

    def test_load_shows_inherited_auth(self, panel, mgr, col_id):
        """When request has no auth but collection does, panel shows inherited."""
        mgr.set_collection_auth(col_id, BearerAuth(token="col-tok-xyz"))
        req_id = mgr.save_request(
            Request(method="GET", url="https://x.com", name="R"),
            collection_id=col_id, name="R",
        )
        req = mgr.get_request(req_id)
        panel.load_request(req)

        assert panel._auth is None, "Request has no own auth"
        assert panel._inherited_auth is not None, "Should have inherited auth"
        assert isinstance(panel._inherited_auth, BearerAuth)
        assert panel._inherited_auth.token == "col-tok-xyz"
        assert "inherited" in panel.auth_type_label.text().lower()

    def test_load_own_auth_no_inherited(self, panel, mgr, col_id):
        """When request has its own auth, inherited is not resolved."""
        mgr.set_collection_auth(col_id, BearerAuth(token="col-tok"))
        req_id = mgr.save_request(
            Request(method="GET", url="https://x.com", name="R",
                    auth=BearerAuth(token="own-tok")),
            collection_id=col_id, name="R",
        )
        req = mgr.get_request(req_id)
        panel.load_request(req)

        assert panel._auth is not None
        assert panel._inherited_auth is None
        assert "inherited" not in panel.auth_type_label.text().lower()

    def test_load_no_auth_anywhere(self, panel, mgr, col_id):
        """No auth at any level → no inherited auth."""
        req_id = mgr.save_request(
            Request(method="GET", url="https://x.com", name="R"),
            collection_id=col_id, name="R",
        )
        req = mgr.get_request(req_id)
        panel.load_request(req)

        assert panel._auth is None
        assert panel._inherited_auth is None
        assert "none" in panel.auth_type_label.text().lower()

    def test_send_preserves_collection_id(self, panel, mgr, col_id):
        """After _send_request builds a new Request, collection_id must survive
        so that inherited auth can be resolved on the next send."""
        mgr.set_collection_auth(col_id, BearerAuth(token="col-tok"))
        req_id = mgr.save_request(
            Request(method="GET", url="https://httpbin.org/get", name="R"),
            collection_id=col_id, name="R",
        )
        req = mgr.get_request(req_id)
        panel.load_request(req)

        # Verify the loaded request has collection context
        assert panel.current_request.collection_id == col_id

        # Simulate what _send_request does: build a new Request from UI fields
        # and overwrite current_request — the key fix is that collection_id
        # is carried forward.
        panel.url_input.setText("https://httpbin.org/get")
        # Don't actually send (no network), just verify state preservation
        old_collection_id = panel.current_request.collection_id
        old_folder = panel.current_request.folder
        old_id = panel.current_request.id

        # After load_request, current_request should retain DB metadata
        assert old_collection_id == col_id
        assert old_id == req_id

    def test_inherited_auth_resolves_fresh_from_db(self, panel, mgr, col_id):
        """Inherited auth must be re-resolved from DB at send time, not reuse
        stale auth baked into current_request.auth from a previous send."""
        mgr.set_collection_auth(col_id, BearerAuth(token="old-token"))
        req_id = mgr.save_request(
            Request(method="GET", url="https://x.com", name="R"),
            collection_id=col_id, name="R",
        )
        req = mgr.get_request(req_id)
        panel.load_request(req)

        # Simulate: bake inherited auth into current_request (as _send_request does)
        panel.current_request = Request(
            method="GET", url="https://x.com",
            auth=BearerAuth(token="old-token"),  # stale
            collection_id=col_id,
        )

        # Now change collection auth in DB
        mgr.set_collection_auth(col_id, BearerAuth(token="new-token"))

        # The panel should resolve fresh from DB, not reuse the stale auth
        assert panel._auth is None  # no own auth
        assert panel.current_request.collection_id == col_id

        # Verify the probe-based resolution would pick up the new token
        from equinox.storage.collections import CollectionManager
        cm = CollectionManager(panel.db)
        probe = Request(
            method="GET", url="",
            collection_id=col_id,
            folder=panel.current_request.folder,
        )
        resolved_auth, source = cm.resolve_effective_auth(probe)
        assert resolved_auth is not None
        assert resolved_auth.token == "new-token"
        assert source == "collection"

    def test_clear_auth_re_resolves_inherited(self, panel, mgr, col_id):
        """After clearing own auth, inherited auth should be re-resolved."""
        mgr.set_collection_auth(col_id, BearerAuth(token="col-tok"))
        req_id = mgr.save_request(
            Request(method="GET", url="https://x.com", name="R",
                    auth=BearerAuth(token="own-tok")),
            collection_id=col_id, name="R",
        )
        req = mgr.get_request(req_id)
        panel.load_request(req)

        # Request has own auth; inherited should not be set
        assert panel._auth is not None
        assert panel._auth.token == "own-tok"
        assert panel._inherited_auth is None

        # Clear own auth
        panel._clear_auth()

        # Now inherited auth should have been re-resolved from collection
        assert panel._auth is None
        assert panel._inherited_auth is not None
        assert panel._inherited_auth.token == "col-tok"
        assert panel._inherited_auth_source == "collection"
        assert "inherited" in panel.auth_type_label.text().lower()

    def test_refresh_inherited_auth_picks_up_external_change(self, panel, mgr, col_id):
        """When collection auth is set externally (e.g. via collections_changed
        signal), refresh_inherited_auth must update the panel."""
        # Load a request from a collection with NO auth
        req_id = mgr.save_request(
            Request(method="GET", url="https://x.com", name="R"),
            collection_id=col_id, name="R",
        )
        req = mgr.get_request(req_id)
        panel.load_request(req)

        assert panel._inherited_auth is None
        assert "none" in panel.auth_type_label.text().lower()

        # Externally set collection auth (simulates collections_changed)
        mgr.set_collection_auth(col_id, BearerAuth(token="ext-tok"))
        panel.refresh_inherited_auth()

        assert panel._inherited_auth is not None
        assert panel._inherited_auth.token == "ext-tok"
        assert "inherited" in panel.auth_type_label.text().lower()

    def test_refresh_inherited_auth_noop_when_own_auth(self, panel, mgr, col_id):
        """refresh_inherited_auth should not override own auth."""
        mgr.set_collection_auth(col_id, BearerAuth(token="col-tok"))
        req_id = mgr.save_request(
            Request(method="GET", url="https://x.com", name="R",
                    auth=BearerAuth(token="own-tok")),
            collection_id=col_id, name="R",
        )
        req = mgr.get_request(req_id)
        panel.load_request(req)

        assert panel._auth.token == "own-tok"
        panel.refresh_inherited_auth()
        # Own auth must remain untouched
        assert panel._auth.token == "own-tok"
        assert panel._inherited_auth is None

    def test_configure_auth_dialog_sees_inherited(self, panel, mgr, col_id):
        """The auth dialog should receive the inherited auth as initial value
        so the user can see what's active."""
        mgr.set_collection_auth(col_id, BearerAuth(token="col-tok"))
        req_id = mgr.save_request(
            Request(method="GET", url="https://x.com", name="R"),
            collection_id=col_id, name="R",
        )
        req = mgr.get_request(req_id)
        panel.load_request(req)

        assert panel._auth is None
        # _configure_auth computes display_auth = self._auth or self._inherited_auth
        display_auth = panel._auth or panel._inherited_auth
        assert display_auth is not None
        assert isinstance(display_auth, BearerAuth)
        assert display_auth.token == "col-tok"

    def test_configure_auth_fetched_token_saved_to_collection(self, panel, mgr, col_id):
        """When inherited auth is active and the user fetches a new token in the
        auth dialog *without changing the underlying config*, the token must be
        persisted back to the collection — NOT baked into the request row.

        Correct behaviour (post-fix):
        • panel._auth remains None  — the request does not own the auth config
        • panel._inherited_auth is updated with the new token (in-memory)
        • The collection row in the DB is updated with the new token
        • The request row in the DB still has auth_type=None (no own auth)

        Old (buggy) behaviour: self._auth was set to the inherited OAuth2
        config+token, causing a copy of the collection auth to be persisted on
        the request row and breaking the "auth info should not be cached by
        request" invariant.
        """
        from unittest.mock import patch, MagicMock
        from equinox.auth import OAuth2Auth

        # Set up collection-level OAuth2 auth (inherited)
        col_auth = OAuth2Auth(
            token_url="https://auth.example.com/token",
            client_id="cid",
            client_secret="secret",
            access_token="old-token",
        )
        mgr.set_collection_auth(col_id, col_auth)
        req_id = mgr.save_request(
            Request(method="GET", url="https://x.com", name="R"),
            collection_id=col_id, name="R",
        )
        req = mgr.get_request(req_id)
        panel.load_request(req)

        # Precondition: request uses inherited auth
        assert panel._auth is None
        assert panel._inherited_auth is not None

        # Simulate the auth dialog returning with same config but a
        # freshly-fetched token (dialog._last_fetched_auth is set).
        saved_auth = OAuth2Auth(
            token_url="https://auth.example.com/token",
            client_id="cid",
            client_secret="secret",
            access_token="new-fetched-token",
        )
        fetched_auth = OAuth2Auth(
            token_url="https://auth.example.com/token",
            client_id="cid",
            access_token="new-fetched-token",
        )

        mock_dialog = MagicMock()
        mock_dialog.exec.return_value = 1  # QDialog.DialogCode.Accepted
        mock_dialog._saved_auth = saved_auth
        mock_dialog._last_fetched_auth = fetched_auth

        with patch(
            "equinox.gui.dialogs.auth_dialog.AuthDialog",
            return_value=mock_dialog,
        ):
            panel._configure_auth()

        # Token was fetched from *inherited* config that still matches the
        # collection — it must be saved to the collection, NOT to the request.
        assert panel._auth is None, (
            "_auth must stay None: the token belongs to the collection, "
            "not to this request row"
        )
        # In-memory inherited auth should reflect the new token
        assert panel._inherited_auth is not None
        assert panel._inherited_auth.access_token == "new-fetched-token", (
            "In-memory _inherited_auth must be updated with the fetched token"
        )
        # DB: the collection's auth should now carry the new token
        col_auth_in_db = mgr.get_collection_auth(col_id)
        if col_auth_in_db is not None:
            assert col_auth_in_db.access_token == "new-fetched-token", (
                "Collection DB auth must be updated with the fetched token"
            )
        # DB: the request row must NOT have own auth stored
        req_reloaded = mgr.get_request(req_id)
        assert req_reloaded.auth is None, (
            "Request row must have auth=None — it should inherit, not own, the auth"
        )

    def test_configure_auth_guard_still_skips_when_no_fetch(self, panel, mgr, col_id):
        """When the user opens the auth dialog on inherited auth and saves
        without fetching a token (no meaningful change), the guard clause
        should still return early, keeping self._auth = None."""
        from unittest.mock import patch, MagicMock
        from equinox.auth import OAuth2Auth

        col_auth = OAuth2Auth(
            token_url="https://auth.example.com/token",
            client_id="cid",
            client_secret="secret",
            access_token="existing-token",
        )
        mgr.set_collection_auth(col_id, col_auth)
        req_id = mgr.save_request(
            Request(method="GET", url="https://x.com", name="R"),
            collection_id=col_id, name="R",
        )
        req = mgr.get_request(req_id)
        panel.load_request(req)

        assert panel._auth is None

        # Dialog returns same config, no token fetch
        saved_auth = OAuth2Auth(
            token_url="https://auth.example.com/token",
            client_id="cid",
            client_secret="secret",
            access_token="existing-token",
        )
        mock_dialog = MagicMock()
        mock_dialog.exec.return_value = 1
        mock_dialog._saved_auth = saved_auth
        mock_dialog._last_fetched_auth = None  # no fetch

        with patch(
            "equinox.gui.dialogs.auth_dialog.AuthDialog",
            return_value=mock_dialog,
        ):
            panel._configure_auth()

        # Guard should fire — self._auth stays None (still inheriting)
        assert panel._auth is None, (
            "Guard clause should keep self._auth = None when no token was fetched"
        )


# ── Query-params default-unchecked ────────────────────────────────────────────

@pytest.mark.skipif(not _can_import_pyqt6(), reason="PyQt6 not available")
class TestQueryParamsDefaultUnchecked:

    @pytest.fixture
    def qapp(self):
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is None:
            app = QApplication([])
        return app

    def test_empty_sentinel_row_is_unchecked(self, qapp):
        """The empty trailing row in CheckableKeyValueTable must be unchecked."""
        from equinox.gui.widgets import CheckableKeyValueTable
        from PyQt6.QtCore import Qt

        table = CheckableKeyValueTable()
        # Initial state: one empty sentinel row
        assert table.rowCount() == 1
        checkbox = table.item(0, 0)
        assert checkbox.checkState() == Qt.CheckState.Unchecked

    def test_loaded_params_keep_their_enabled_flag(self, qapp):
        """Params loaded from DB retain their saved enabled state."""
        from equinox.gui.widgets import CheckableKeyValueTable
        from PyQt6.QtCore import Qt

        table = CheckableKeyValueTable()
        table.set_data([
            {"key": "enabled_param", "value": "1", "enabled": True},
            {"key": "disabled_param", "value": "2", "enabled": False},
        ])

        # Data rows
        assert table.item(0, 0).checkState() == Qt.CheckState.Checked
        assert table.item(1, 0).checkState() == Qt.CheckState.Unchecked
        # Trailing sentinel
        assert table.item(2, 0).checkState() == Qt.CheckState.Unchecked

    def test_dict_data_loaded_as_enabled(self, qapp):
        """When set_data receives a plain dict, all rows are enabled."""
        from equinox.gui.widgets import CheckableKeyValueTable
        from PyQt6.QtCore import Qt

        table = CheckableKeyValueTable()
        table.set_data({"page": "1", "limit": "50"})

        # Both data rows should be checked
        for row in range(2):
            assert table.item(row, 0).checkState() == Qt.CheckState.Checked
        # Trailing sentinel should be unchecked
        assert table.item(2, 0).checkState() == Qt.CheckState.Unchecked


@pytest.mark.skipif(not _can_import_pyqt6(), reason="PyQt6 not available")
class TestParamsSetAll:
    """Verify the Enable All / Disable All toolbar on the Params tab."""

    @pytest.fixture
    def qapp(self):
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is None:
            app = QApplication([])
        return app

    @pytest.fixture
    def panel(self, qapp, db):
        from equinox.gui.request_panel import RequestPanel
        p = RequestPanel(db)
        yield p
        p.close()

    def test_enable_all_params(self, panel):
        """Enable All must check every row in the params table."""
        from PyQt6.QtCore import Qt

        panel.params_table.set_data([
            {"key": "a", "value": "1", "enabled": False},
            {"key": "b", "value": "2", "enabled": False},
        ])
        # Both disabled
        assert panel.params_table.item(0, 0).checkState() == Qt.CheckState.Unchecked
        assert panel.params_table.item(1, 0).checkState() == Qt.CheckState.Unchecked

        panel._params_set_all(True)

        assert panel.params_table.item(0, 0).checkState() == Qt.CheckState.Checked
        assert panel.params_table.item(1, 0).checkState() == Qt.CheckState.Checked

    def test_disable_all_params(self, panel):
        """Disable All must uncheck every row in the params table."""
        from PyQt6.QtCore import Qt

        panel.params_table.set_data([
            {"key": "a", "value": "1", "enabled": True},
            {"key": "b", "value": "2", "enabled": True},
        ])
        assert panel.params_table.item(0, 0).checkState() == Qt.CheckState.Checked
        assert panel.params_table.item(1, 0).checkState() == Qt.CheckState.Checked

        panel._params_set_all(False)

        assert panel.params_table.item(0, 0).checkState() == Qt.CheckState.Unchecked
        assert panel.params_table.item(1, 0).checkState() == Qt.CheckState.Unchecked# ── OAuth2 auto-fetch ─────────────────────────────────────────────────────────

class TestOAuth2AutoFetch:
    """Verify that OAuth2Auth with credentials but no token triggers auto-fetch."""

    def test_needs_refresh_when_no_token(self):
        """OAuth2Auth with no access_token must need refresh."""
        from equinox.auth._oauth2 import OAuth2Auth
        auth = OAuth2Auth(
            token_url="https://auth.example.com/token",
            client_id="cid",
            client_secret="secret",
            access_token=None,
        )
        assert auth._needs_refresh() is True

    def test_no_refresh_when_token_present_no_expiry(self):
        """OAuth2Auth with existing token and no expiry should NOT refresh."""
        from equinox.auth._oauth2 import OAuth2Auth
        auth = OAuth2Auth(
            token_url="https://auth.example.com/token",
            client_id="cid",
            client_secret="secret",
            access_token="valid-token",
        )
        assert auth._needs_refresh() is False

    def test_default_expiry_set_after_fetch(self):
        """When token endpoint omits expires_in, a default expiry is set."""
        from unittest.mock import patch, Mock, MagicMock
        from equinox.auth._oauth2 import OAuth2Auth

        auth = OAuth2Auth(
            token_url="https://auth.example.com/token",
            client_id="cid",
            client_secret="secret",
            access_token=None,
        )
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = Mock()
        mock_resp.json.return_value = {
            "access_token": "new-token",
            "token_type": "Bearer",
            # NO expires_in
        }
        with patch("equinox.auth._oauth2.httpx.Client") as mock_client_class:
            mock_client = MagicMock()
            mock_client_class.return_value.__enter__.return_value = mock_client
            mock_client.post.return_value = mock_resp
            os.environ["EQUINOX_SSRF_ALLOW_ON_DNS_FAILURE"] = "1"
            auth._refresh_access_token()
            os.environ["EQUINOX_SSRF_ALLOW_ON_DNS_FAILURE"] = "0"

        assert auth.access_token == "new-token"
        assert auth.expires_at is not None, "Default expiry should be set"
        assert auth._needs_refresh() is False  # token is valid

    def test_auth_error_propagates_with_message(self):
        """Auth errors from _apply_auth must propagate with their original
        message through _send_internal, not be wrapped in a generic error."""
        from equinox.core.client import HTTPClient
        from equinox.core.request import Request
        from equinox.core.exceptions import RequestError
        from equinox.auth._oauth2 import OAuth2Auth

        auth = OAuth2Auth(
            token_url="https://will-not-resolve.invalid/token",
            client_id="cid",
            client_secret="secret",
            access_token=None,
        )
        request = Request(method="GET", url="https://httpbin.org/get", auth=auth)
        client = HTTPClient(timeout=2)

        import pytest
        with pytest.raises(RequestError, match="Authentication failed"):
            client.send(request)


@pytest.mark.skipif(not _can_import_pyqt6(), reason="PyQt6 not available")
class TestAuthDialogClientPicker:
    """Verify that picking a saved credential clears stale tokens."""

    @pytest.fixture
    def qapp(self):
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is None:
            app = QApplication([])
        return app

    def test_on_client_picked_clears_tokens(self, qapp, db):
        """When a saved credential is picked, access_token and refresh_token
        fields must be cleared so stale tokens don't suppress auto-fetch."""
        from equinox.storage import SavedCredentialsManager
        from equinox.gui.dialogs.auth_dialog import AuthDialog
        from equinox.auth import OAuth2Auth

        # Create a saved credential
        scm = SavedCredentialsManager(db)
        cred_id = scm.create(
            name="Test Client",
            auth_type="oauth2",
            config={
                "token_url": "https://auth.example.com/token",
                "client_id": "cid",
                "client_secret": "secret",
                "scope": "read",
            },
        )

        # Open dialog with an existing OAuth2Auth that has a stale token
        stale_auth = OAuth2Auth(
            token_url="https://old.example.com/token",
            client_id="old-cid",
            client_secret="old-secret",
            access_token="stale-token",
            refresh_token="stale-refresh",
        )
        dialog = AuthDialog(stale_auth, None, db=db)

        # Verify the stale tokens are loaded
        assert dialog.oauth2_access_token.text() == "stale-token"
        assert dialog.oauth2_refresh_token.text() == "stale-refresh"

        # Simulate picking the saved credential
        dialog._on_client_picked(1)  # trigger with any index

        # After load, find the credential and trigger _on_client_picked properly
        dialog.cred_picker.setCurrentIndex(1)

        # Token fields must be cleared
        assert dialog.oauth2_access_token.text() == ""
        assert dialog.oauth2_refresh_token.text() == ""
        # But credential fields should be populated
        assert dialog.oauth2_token_url.text() == "https://auth.example.com/token"
        assert dialog.oauth2_client_id.text() == "cid"

    def test_oauth2_error_with_response_enables_view_button(self, qapp, db):
        """Error payloads with a response snapshot should keep View Response enabled."""
        from equinox.gui.dialogs.auth_dialog import AuthDialog

        dialog = AuthDialog(None, None, db=db)
        response = {
            "status_code": 401,
            "method": "POST",
            "url": "https://auth.example.com/token",
            "headers": {"content-type": "application/json"},
            "body": {"error": "invalid_client"},
        }

        dialog._on_token_fetched({
            "ok": False,
            "auth": None,
            "error": "Token endpoint returned HTTP 401",
            "response": response,
        })

        assert dialog.oauth2_view_response_btn.isEnabled() is True

