"""Tests for OAuthClientManager and SavedCredentialsManager."""

import sqlite3
from typing import cast

import pytest

from equinox.core.exceptions import StorageError, ValidationError
from equinox.storage.database import Database
from equinox.storage.oauth_clients import OAuthClientManager
from equinox.storage.saved_credentials import SavedCredentialsManager


@pytest.fixture
def db(tmp_path):
    return Database(str(tmp_path / "test.db"))


# ── OAuthClientManager ───────────────────────────────────────────────────────
class TestOAuthClientManager:
    def test_create_and_get(self, db):
        mgr = OAuthClientManager(db)
        cid = mgr.create_client(
            name="My Client",
            token_url="https://auth.example.com/token",
            client_id="cid123",
            client_secret="sec456",
            scope="read write",
            grant_type="client_credentials",
            token_auth="basic",
            verify_ssl=False,
            description="Test client",
        )
        assert cid >= 1
        client = mgr.get_client(cid)
        assert client is not None
        assert client["name"] == "My Client"
        assert client["client_id"] == "cid123"
        assert client["grant_type"] == "client_credentials"
        assert client["token_auth"] == "basic"
        assert client["verify_ssl"] is False

    def test_list_clients(self, db):
        mgr = OAuthClientManager(db)
        mgr.create_client(name="A", token_url="", client_id="", client_secret="")
        mgr.create_client(name="B", token_url="", client_id="", client_secret="")
        clients = mgr.list_clients()
        assert len(clients) == 2

    def test_duplicate_name_raises(self, db):
        mgr = OAuthClientManager(db)
        mgr.create_client(name="Dup", token_url="", client_id="", client_secret="")
        with pytest.raises(StorageError, match="already exists"):
            mgr.create_client(name="Dup", token_url="", client_id="", client_secret="")

    def test_update_client(self, db):
        mgr = OAuthClientManager(db)
        cid = mgr.create_client(name="Old", token_url="", client_id="", client_secret="")
        mgr.update_client(cid, name="New")
        client = mgr.get_client(cid)
        assert client["name"] == "New"

    def test_delete_client(self, db):
        mgr = OAuthClientManager(db)
        cid = mgr.create_client(name="ToDelete", token_url="", client_id="", client_secret="")
        mgr.delete_client(cid)
        assert mgr.get_client(cid) is None

    def test_set_default(self, db):
        mgr = OAuthClientManager(db)
        c1 = mgr.create_client(name="C1", token_url="", client_id="", client_secret="")
        c2 = mgr.create_client(name="C2", token_url="", client_id="", client_secret="")
        mgr.set_default(c1)
        assert mgr.get_default()["id"] == c1
        mgr.set_default(c2)
        assert mgr.get_default()["id"] == c2

    def test_invalid_grant_type(self, db):
        mgr = OAuthClientManager(db)
        with pytest.raises(ValidationError):
            mgr.create_client(
                name="Bad", token_url="", client_id="", client_secret="", grant_type="invalid_grant"
            )

    def test_get_nonexistent(self, db):
        mgr = OAuthClientManager(db)
        assert mgr.get_client(9999) is None

    def test_get_client_by_name(self, db):
        mgr = OAuthClientManager(db)
        mgr.create_client(
            name="ByName", token_url="https://t.com/token", client_id="cid", client_secret="sec"
        )
        client = mgr.get_client_by_name("ByName")
        assert client is not None
        assert client["client_id"] == "cid"
        assert mgr.get_client_by_name("NonExistent") is None

    def test_clear_default(self, db):
        mgr = OAuthClientManager(db)
        cid = mgr.create_client(name="CD", token_url="", client_id="", client_secret="")
        mgr.set_default(cid)
        assert mgr.get_default() is not None
        mgr.clear_default()
        assert mgr.get_default() is None

    def test_update_multiple_fields(self, db):
        mgr = OAuthClientManager(db)
        cid = mgr.create_client(
            name="Multi",
            token_url="https://old.com",
            client_id="old_id",
            client_secret="old_sec",
            scope="read",
            grant_type="client_credentials",
        )
        mgr.update_client(
            cid,
            token_url="https://new.com",
            client_id_val="new_id",
            client_secret="new_sec",
            scope="read write",
            grant_type="password",
            token_auth="basic",
            verify_ssl=False,
            description="Updated desc",
        )
        client = mgr.get_client(cid)
        assert client["token_url"] == "https://new.com"
        assert client["client_id"] == "new_id"
        assert client["client_secret"] == "new_sec"
        assert client["scope"] == "read write"
        assert client["grant_type"] == "password"
        assert client["token_auth"] == "basic"
        assert client["verify_ssl"] is False
        assert client["description"] == "Updated desc"

    def test_update_extra_params(self, db):
        mgr = OAuthClientManager(db)
        cid = mgr.create_client(
            name="EP",
            token_url="",
            client_id="",
            client_secret="",
            extra_params={"audience": "https://api.example.com"},
        )
        client = mgr.get_client(cid)
        assert client["extra_params"] == {"audience": "https://api.example.com"}
        mgr.update_client(cid, extra_params={"resource": "new_res"})
        client = mgr.get_client(cid)
        assert client["extra_params"] == {"resource": "new_res"}

    def test_update_no_fields_noop(self, db):
        mgr = OAuthClientManager(db)
        cid = mgr.create_client(name="NoOp", token_url="", client_id="", client_secret="")
        mgr.update_client(cid)  # should not raise
        assert mgr.get_client(cid)["name"] == "NoOp"

    def test_update_nonexistent_raises(self, db):
        mgr = OAuthClientManager(db)
        with pytest.raises(StorageError, match="not found"):
            mgr.update_client(9999, name="New")

    def test_delete_nonexistent_raises(self, db):
        mgr = OAuthClientManager(db)
        with pytest.raises(StorageError, match="not found"):
            mgr.delete_client(9999)

    def test_set_default_nonexistent_raises(self, db):
        mgr = OAuthClientManager(db)
        with pytest.raises(StorageError, match="not found"):
            mgr.set_default(9999)

    def test_update_invalid_grant_type(self, db):
        mgr = OAuthClientManager(db)
        cid = mgr.create_client(name="GT", token_url="", client_id="", client_secret="")
        with pytest.raises(ValidationError, match="grant_type"):
            mgr.update_client(cid, grant_type="bad_grant")

    def test_to_oauth2_auth(self, db):
        mgr = OAuthClientManager(db)
        cid = mgr.create_client(
            name="Auth",
            token_url="https://auth.com/token",
            client_id="cid",
            client_secret="csec",
            scope="openid",
        )
        client = mgr.get_client(cid)
        assert client is not None
        auth_obj = mgr.to_oauth2_auth(cast(dict, client))
        from equinox.auth._oauth2 import OAuth2Auth

        assert isinstance(auth_obj, OAuth2Auth)
        assert auth_obj.token_url == "https://auth.com/token"
        assert auth_obj.client_id == "cid"
        assert auth_obj.client_secret == "csec"
        assert auth_obj.token_auth == "body"
        assert auth_obj.verify_ssl is True

    def test_token_auth_and_verify_ssl_propagate_to_oauth2_auth(self, db):
        mgr = OAuthClientManager(db)
        cid = mgr.create_client(
            name="AuthMode",
            token_url="https://auth.com/token",
            client_id="cid",
            client_secret="csec",
            token_auth="basic",
            verify_ssl=False,
        )
        client = mgr.get_client(cid)
        assert client is not None
        auth_obj = mgr.to_oauth2_auth(cast(dict, client))
        assert auth_obj.token_auth == "basic"
        assert auth_obj.verify_ssl is False

    def test_name_required(self, db):
        mgr = OAuthClientManager(db)
        with pytest.raises(ValidationError, match="required|non-empty"):
            mgr.create_client(name="", token_url="", client_id="", client_secret="")

    def test_name_too_long(self, db):
        mgr = OAuthClientManager(db)
        with pytest.raises(ValidationError, match="too long"):
            mgr.create_client(name="x" * 201, token_url="", client_id="", client_secret="")

    def test_update_duplicate_name_raises(self, db):
        mgr = OAuthClientManager(db)
        mgr.create_client(name="First", token_url="", client_id="", client_secret="")
        c2 = mgr.create_client(name="Second", token_url="", client_id="", client_secret="")
        with pytest.raises(StorageError, match="already exists"):
            mgr.update_client(c2, name="First")

    def test_extra_params_default_empty(self, db):
        mgr = OAuthClientManager(db)
        cid = mgr.create_client(name="NoExtra", token_url="", client_id="", client_secret="")
        client = mgr.get_client(cid)
        assert client is not None
        assert client["extra_params"] == {}

    def test_client_secret_is_encrypted_at_rest(self, db):
        mgr = OAuthClientManager(db)
        cid = mgr.create_client(
            name="EncSecret", token_url="", client_id="", client_secret="top-secret"
        )
        row = db.fetchone("SELECT client_secret FROM oauth_clients WHERE id = ?", (cid,))
        assert row is not None
        assert row["client_secret"].startswith("enc:")

    def test_legacy_plaintext_secret_is_migrated_on_read(self, db):
        mgr = OAuthClientManager(db)
        legacy_id: int = mgr.create_client(
            name="LegacyPlain", token_url="", client_id="", client_secret="temp-secret"
        )
        with sqlite3.connect(str(db.db_path)) as conn:
            conn.execute(
                "UPDATE oauth_clients SET client_secret = ? WHERE id = ?",
                ("plain-legacy", legacy_id),
            )
            conn.commit()

        client = mgr.get_client(legacy_id)
        assert client is not None
        assert client["client_secret"] == "plain-legacy"

        row = db.fetchone("SELECT client_secret FROM oauth_clients WHERE id = ?", (legacy_id,))
        assert row is not None
        assert row["client_secret"].startswith("enc:")


# ── SavedCredentialsManager ──────────────────────────────────────────────────
class TestSavedCredentialsManager:
    def test_create_bearer(self, db):
        mgr = SavedCredentialsManager(db)
        cid = mgr.create_credential(
            name="My Token",
            auth_type="bearer",
            config={"token": "abc123"},
            description="Test bearer",
        )
        assert cid >= 1
        cred = mgr.get_credential(cid)
        assert cred["name"] == "My Token"
        assert cred["auth_type"] == "bearer"
        assert cred["config"]["token"] == "abc123"

    def test_create_basic(self, db):
        mgr = SavedCredentialsManager(db)
        cid = mgr.create_credential(
            name="Basic Cred",
            auth_type="basic",
            config={"username": "user", "password": "pass"},
        )
        cred = mgr.get_credential(cid)
        assert cred["config"]["username"] == "user"

    def test_create_api_key(self, db):
        mgr = SavedCredentialsManager(db)
        cid = mgr.create_credential(
            name="API Key",
            auth_type="api_key",
            config={"key": "X-API-Key", "value": "secret", "location": "header"},
        )
        cred = mgr.get_credential(cid)
        assert cred["config"]["location"] == "header"

    def test_list_and_delete(self, db):
        mgr = SavedCredentialsManager(db)
        mgr.create_credential(name="C1", auth_type="bearer", config={"token": "a"})
        mgr.create_credential(name="C2", auth_type="bearer", config={"token": "b"})
        assert len(mgr.list_credentials()) == 2
        creds = mgr.list_credentials()
        mgr.delete_credential(creds[0]["id"])
        assert len(mgr.list_credentials()) == 1

    def test_update_credential(self, db):
        mgr = SavedCredentialsManager(db)
        cid = mgr.create_credential(name="Old", auth_type="bearer", config={"token": "old"})
        mgr.update_credential(cid, config={"token": "new"})
        cred = mgr.get_credential(cid)
        assert cred["config"]["token"] == "new"

    def test_duplicate_name_raises(self, db):
        mgr = SavedCredentialsManager(db)
        mgr.create_credential(name="Dup", auth_type="bearer", config={"token": "x"})
        with pytest.raises(StorageError):
            mgr.create_credential(name="Dup", auth_type="bearer", config={"token": "y"})

    def test_invalid_auth_type(self, db):
        mgr = SavedCredentialsManager(db)
        with pytest.raises(ValidationError):
            mgr.create_credential(name="Bad", auth_type="unknown", config={})
