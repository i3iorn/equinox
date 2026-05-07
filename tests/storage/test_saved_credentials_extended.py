"""Extended tests for SavedCredentialsManager — covers get_by_name, get_default,
list filtering, set_default, clear_default, duplicate, to_auth_strategy."""

import pytest

from equinox.storage.database import Database
from equinox.storage.saved_credentials import SavedCredentialsManager
from equinox.core.exceptions import StorageError, ValidationError
from equinox.auth import OAuth2Auth, APIKeyAuth, BasicAuth, BearerAuth


@pytest.fixture
def db(tmp_path):
    return Database(str(tmp_path / "test.db"))


@pytest.fixture
def mgr(db):
    return SavedCredentialsManager(db)


def _create(mgr, name="My Cred", auth_type="bearer", config=None, description="", is_default=False):
    return mgr.create(
        name=name,
        auth_type=auth_type,
        config=config or {"token": "abc"},
        description=description,
        is_default=is_default,
    )


# ── create validation ─────────────────────────────────────────────────────────

class TestCreate:

    def test_empty_name_raises(self, mgr):
        with pytest.raises(ValidationError):
            mgr.create(name="", auth_type="bearer", config={})

    def test_whitespace_name_raises(self, mgr):
        with pytest.raises(ValidationError):
            mgr.create(name="   ", auth_type="bearer", config={})

    def test_name_too_long_raises(self, mgr):
        with pytest.raises(ValidationError):
            mgr.create(name="x" * 201, auth_type="bearer", config={})

    def test_description_too_long_raises(self, mgr):
        with pytest.raises(ValidationError):
            mgr.create(name="ok", auth_type="bearer", config={}, description="d" * 1001)

    def test_duplicate_name_raises_storage_error(self, mgr):
        _create(mgr, "Dup")
        with pytest.raises(StorageError, match="already exists"):
            _create(mgr, "Dup")

    def test_is_default_flag(self, mgr):
        cid = mgr.create(name="Default Cred", auth_type="bearer",
                          config={"token": "t"}, is_default=True)
        cred = mgr.get(cid)
        assert cred["is_default"] is True


# ── get / get_by_name / get_default ──────────────────────────────────────────

class TestGetMethods:

    def test_get_nonexistent_returns_none(self, mgr):
        assert mgr.get(9999) is None

    def test_get_by_name(self, mgr):
        _create(mgr, "Named Cred")
        cred = mgr.get_by_name("Named Cred")
        assert cred is not None
        assert cred["name"] == "Named Cred"

    def test_get_by_name_nonexistent(self, mgr):
        assert mgr.get_by_name("does-not-exist") is None

    def test_get_default_none_when_empty(self, mgr):
        assert mgr.get_default() is None

    def test_get_default(self, mgr):
        cid = _create(mgr, "Default One", is_default=True)
        default = mgr.get_default()
        assert default is not None
        assert default["id"] == cid

    def test_get_default_by_auth_type(self, mgr):
        mgr.create(name="OAuth Default", auth_type="oauth2",
                   config={"token_url": "https://t.example.com", "client_id": "cid"},
                   is_default=True)
        mgr.create(name="Bearer Default", auth_type="bearer",
                   config={"token": "x"}, is_default=True)
        d = mgr.get_default(auth_type="oauth2")
        assert d["auth_type"] == "oauth2"

    def test_get_default_by_auth_type_no_match(self, mgr):
        _create(mgr, "Bearer Only", auth_type="bearer", is_default=True)
        assert mgr.get_default(auth_type="basic") is None


# ── list ──────────────────────────────────────────────────────────────────────

class TestList:

    def test_list_all(self, mgr):
        _create(mgr, "A", auth_type="bearer")
        _create(mgr, "B", auth_type="basic", config={"username": "u", "password": "p"})
        result = mgr.list()
        assert len(result) == 2

    def test_list_filter_by_auth_type(self, mgr):
        _create(mgr, "Bearer1", auth_type="bearer")
        _create(mgr, "Bearer2", auth_type="bearer")
        _create(mgr, "Basic1", auth_type="basic", config={"username": "u", "password": "p"})
        bearers = mgr.list(auth_type="bearer")
        assert len(bearers) == 2
        assert all(c["auth_type"] == "bearer" for c in bearers)

    def test_list_empty(self, mgr):
        assert mgr.list() == []


# ── update ────────────────────────────────────────────────────────────────────

class TestUpdate:

    def test_update_config(self, mgr):
        cid = _create(mgr, "To Update")
        mgr.update(cid, config={"token": "new_token"})
        cred = mgr.get(cid)
        assert cred["config"]["token"] == "new_token"

    def test_update_name(self, mgr):
        cid = _create(mgr, "Old Name")
        mgr.update(cid, name="New Name")
        cred = mgr.get(cid)
        assert cred["name"] == "New Name"

    def test_update_auth_type(self, mgr):
        cid = _create(mgr, "Change Type", auth_type="bearer")
        mgr.update(cid, auth_type="basic", config={"username": "u", "password": "p"})
        cred = mgr.get(cid)
        assert cred["auth_type"] == "basic"

    def test_update_description(self, mgr):
        cid = _create(mgr, "Desc Update")
        mgr.update(cid, description="New description")
        cred = mgr.get(cid)
        assert cred["description"] == "New description"

    def test_update_nonexistent_raises(self, mgr):
        with pytest.raises(StorageError):
            mgr.update(9999, config={"token": "x"})

    def test_update_invalid_auth_type_raises(self, mgr):
        cid = _create(mgr, "Bad Type Update")
        with pytest.raises(ValidationError):
            mgr.update(cid, auth_type="unknown_type")

    def test_update_no_changes_is_no_op(self, mgr):
        cid = _create(mgr, "No Change")
        mgr.update(cid)  # nothing passed — should not raise
        assert mgr.get(cid) is not None

    def test_update_empty_name_raises(self, mgr):
        cid = _create(mgr, "Non-empty")
        with pytest.raises(ValidationError):
            mgr.update(cid, name="")

    def test_update_duplicate_name_raises(self, mgr):
        _create(mgr, "Existing")
        cid2 = _create(mgr, "Target")
        with pytest.raises(StorageError):
            mgr.update(cid2, name="Existing")


# ── set_default / clear_default ──────────────────────────────────────────────

class TestDefaultManagement:

    def test_set_default(self, mgr):
        c1 = _create(mgr, "Cred1")
        c2 = _create(mgr, "Cred2")
        mgr.set_default(c1)
        assert mgr.get(c1)["is_default"] is True
        mgr.set_default(c2)
        assert mgr.get(c2)["is_default"] is True
        assert mgr.get(c1)["is_default"] is False  # previous default cleared

    def test_set_default_nonexistent_raises(self, mgr):
        with pytest.raises(StorageError):
            mgr.set_default(9999)

    def test_clear_default(self, mgr):
        cid = _create(mgr, "Default", is_default=True)
        mgr.clear_default()
        assert mgr.get(cid)["is_default"] is False
        assert mgr.get_default() is None


# ── duplicate / _unique_copy_name ─────────────────────────────────────────────

class TestDuplicate:

    def test_duplicate_basic(self, mgr):
        cid = _create(mgr, "Original", auth_type="bearer", config={"token": "tok"})
        new_id = mgr.duplicate(cid)
        assert new_id != cid
        copy = mgr.get(new_id)
        assert "Original" in copy["name"]
        assert copy["config"]["token"] == "tok"

    def test_duplicate_with_new_name(self, mgr):
        cid = _create(mgr, "Source")
        new_id = mgr.duplicate(cid, new_name="Custom Copy")
        copy = mgr.get(new_id)
        assert copy["name"] == "Custom Copy"

    def test_duplicate_nonexistent_raises(self, mgr):
        with pytest.raises(StorageError):
            mgr.duplicate(9999)

    def test_duplicate_twice_creates_unique_names(self, mgr):
        cid = _create(mgr, "Base")
        id1 = mgr.duplicate(cid)
        id2 = mgr.duplicate(cid)
        names = {mgr.get(id1)["name"], mgr.get(id2)["name"]}
        assert len(names) == 2  # both unique


# ── delete ────────────────────────────────────────────────────────────────────

class TestDelete:

    def test_delete_existing(self, mgr):
        cid = _create(mgr, "To Delete")
        mgr.delete(cid)
        assert mgr.get(cid) is None

    def test_delete_nonexistent_raises(self, mgr):
        with pytest.raises(StorageError):
            mgr.delete(9999)


# ── to_auth_strategy ──────────────────────────────────────────────────────────

class TestToAuthStrategy:

    def test_bearer(self, mgr):
        cid = mgr.create(name="Bearer", auth_type="bearer", config={"token": "abc123"})
        row = mgr.get(cid)
        auth = mgr.to_auth_strategy(row)
        assert isinstance(auth, BearerAuth)
        assert auth.token == "abc123"

    def test_basic(self, mgr):
        cid = mgr.create(name="Basic", auth_type="basic",
                          config={"username": "user", "password": "pass"})
        row = mgr.get(cid)
        auth = mgr.to_auth_strategy(row)
        assert isinstance(auth, BasicAuth)
        assert auth.username == "user"

    def test_api_key(self, mgr):
        cid = mgr.create(name="API Key", auth_type="api_key",
                          config={"key": "X-API-Key", "value": "secret", "location": "header"})
        row = mgr.get(cid)
        auth = mgr.to_auth_strategy(row)
        assert isinstance(auth, APIKeyAuth)
        assert auth.location == "header"

    def test_oauth2(self, mgr):
        cid = mgr.create(name="OAuth", auth_type="oauth2",
                          config={"token_url": "https://auth.example.com/token",
                                  "client_id": "cid123", "client_secret": "sec"})
        row = mgr.get(cid)
        auth = mgr.to_auth_strategy(row)
        assert isinstance(auth, OAuth2Auth)

    def test_unknown_auth_type_raises(self, mgr):
        row = {"auth_type": "ftp", "config": {}}
        with pytest.raises(ValidationError):
            mgr.to_auth_strategy(row)


# ── _decode edge cases ────────────────────────────────────────────────────────

class TestDecode:

    def test_config_empty_string_decoded_to_empty_dict(self, mgr):
        cid = mgr.create(name="Empty Config", auth_type="bearer", config={})
        row = mgr.get(cid)
        assert isinstance(row["config"], dict)

    def test_is_default_is_bool(self, mgr):
        cid = _create(mgr, "Bool Test")
        row = mgr.get(cid)
        assert isinstance(row["is_default"], bool)
