"""Tests for auth credential encryption at the serialization boundary.

Verifies:
- _serialize_auth produces encrypted (``enc:…``) blobs
- _deserialize_auth transparently decrypts them
- Legacy plaintext JSON is still readable (graceful migration)
- Saved credentials config column is encrypted
- Round-trip preserves all auth fields
"""

import json
import pytest

from equinox.storage.database import Database
from equinox.storage.collections import CollectionManager
from equinox.storage.saved_credentials import SavedCredentialsManager
from equinox.auth import BearerAuth, BasicAuth, APIKeyAuth
from equinox.auth._oauth2 import OAuth2Auth
from equinox.core.auth_cipher import encrypt_auth_data, decrypt_auth_data


@pytest.fixture
def db(tmp_path):
    db_path = tmp_path / "test_enc.db"
    return Database(str(db_path))


@pytest.fixture
def mgr(db):
    return CollectionManager(db)


@pytest.fixture
def col_id(mgr):
    return mgr.create_collection("EncTest")


# ── Low-level cipher ──────────────────────────────────────────────────────────


class TestAuthCipher:
    def test_encrypt_produces_enc_prefix(self):
        plain = '{"type":"bearer","token":"abc"}'
        enc = encrypt_auth_data(plain)
        assert enc.startswith("enc:")
        assert enc != plain

    def test_round_trip(self):
        plain = '{"type":"basic","username":"u","password":"p"}'
        assert decrypt_auth_data(encrypt_auth_data(plain)) == plain

    def test_plaintext_passthrough(self):
        """Legacy unencrypted JSON is returned as-is."""
        plain = '{"type":"bearer","token":"legacy"}'
        assert decrypt_auth_data(plain) == plain

    def test_empty_string(self):
        assert decrypt_auth_data("") == ""

    def test_none_passthrough(self):
        # decrypt_auth_data guards on falsy input
        assert decrypt_auth_data(None) is None


# ── Serialize / deserialize boundary ──────────────────────────────────────────


class TestSerializeBoundary:
    def test_auth_data_column_is_encrypted(self, mgr, col_id):
        """The raw auth_data stored in the DB should start with 'enc:'."""
        auth = BearerAuth(token="super-secret-token")
        mgr.set_collection_auth(col_id, auth)
        # Read raw column — not through _deserialize_auth
        row = mgr.db.fetchone(
            "SELECT auth_data FROM collections WHERE id = ?", (col_id,)
        )
        raw = row["auth_data"]
        assert raw.startswith("enc:"), f"auth_data should be encrypted, got: {raw[:40]}"

    def test_round_trip_bearer(self, mgr, col_id):
        auth = BearerAuth(token="tok-123")
        mgr.set_collection_auth(col_id, auth)
        result = mgr.get_collection_auth(col_id)
        assert isinstance(result, BearerAuth)
        assert result.token == "tok-123"

    def test_round_trip_basic(self, mgr, col_id):
        auth = BasicAuth(username="admin", password="s3cret!")
        mgr.set_collection_auth(col_id, auth)
        result = mgr.get_collection_auth(col_id)
        assert isinstance(result, BasicAuth)
        assert result.username == "admin"
        assert result.password == "s3cret!"

    def test_round_trip_oauth2(self, mgr, col_id):
        auth = OAuth2Auth(
            token_url="https://auth.example.com/token",
            client_id="cid",
            client_secret="csec",
            scope="read write",
            access_token="at-xyz",
            refresh_token="rt-abc",
            token_timeout=15.0,
        )
        mgr.set_collection_auth(col_id, auth)
        result = mgr.get_collection_auth(col_id)
        assert isinstance(result, OAuth2Auth)
        assert result.client_id == "cid"
        assert result.client_secret == "csec"
        assert result.access_token == "at-xyz"
        assert result.refresh_token == "rt-abc"
        assert result.token_timeout == 15.0

    def test_round_trip_api_key(self, mgr, col_id):
        auth = APIKeyAuth(key="X-Api-Key", value="key-val", location="header")
        mgr.set_collection_auth(col_id, auth)
        result = mgr.get_collection_auth(col_id)
        assert isinstance(result, APIKeyAuth)
        assert result.key == "X-Api-Key"
        assert result.value == "key-val"

    def test_legacy_plaintext_readable(self, db, mgr, col_id):
        """Simulate pre-encryption data: write plaintext JSON directly."""
        plain_json = json.dumps({"type": "bearer", "token": "legacy-tok"})
        db.execute(
            "UPDATE collections SET auth_type=?, auth_data=? WHERE id=?",
            ("bearer", plain_json, col_id),
        )
        result = mgr.get_collection_auth(col_id)
        assert isinstance(result, BearerAuth)
        assert result.token == "legacy-tok"

    def test_request_auth_encrypted(self, mgr, col_id):
        """Request-level auth_data is also encrypted."""
        from equinox.core.request import Request
        req_id = mgr.save_request(
            Request(
                method="GET", url="https://api.example.com",
                name="R", auth=BearerAuth(token="req-secret"),
            ),
            collection_id=col_id, name="R",
        )
        row = mgr.db.fetchone("SELECT auth_data FROM requests WHERE id=?", (req_id,))
        assert row["auth_data"].startswith("enc:")
        loaded = mgr.get_request(req_id)
        assert loaded.auth.token == "req-secret"


# ── Saved credentials encryption ──────────────────────────────────────────────


class TestSavedCredentialsEncryption:
    def test_config_column_encrypted(self, db):
        scm = SavedCredentialsManager(db)
        cid = scm.create(
            name="Test Bearer",
            auth_type="bearer",
            config={"token": "saved-secret"},
        )
        row = db.fetchone("SELECT config FROM saved_credentials WHERE id=?", (cid,))
        raw = row["config"]
        assert raw.startswith("enc:"), f"config should be encrypted, got: {raw[:40]}"

    def test_round_trip_saved_credential(self, db):
        scm = SavedCredentialsManager(db)
        cid = scm.create(
            name="Test OAuth2",
            auth_type="oauth2",
            config={"token_url": "https://auth.test/token",
                    "client_id": "cid", "client_secret": "csec"},
        )
        result = scm.get(cid)
        assert result["config"]["client_secret"] == "csec"

    def test_update_re_encrypts(self, db):
        scm = SavedCredentialsManager(db)
        cid = scm.create(
            name="Updatable",
            auth_type="bearer",
            config={"token": "old-token"},
        )
        scm.update(cid, config={"token": "new-token"})
        row = db.fetchone("SELECT config FROM saved_credentials WHERE id=?", (cid,))
        assert row["config"].startswith("enc:")
        result = scm.get(cid)
        assert result["config"]["token"] == "new-token"

    def test_legacy_plaintext_config(self, db):
        """Simulate pre-encryption saved credential."""
        scm = SavedCredentialsManager(db)
        cid = scm.create(name="Legacy", auth_type="bearer", config={"token": "x"})
        # Overwrite with raw plaintext
        plain = json.dumps({"token": "legacy-saved"})
        db.execute(
            "UPDATE saved_credentials SET config=? WHERE id=?", (plain, cid)
        )
        result = scm.get(cid)
        assert result["config"]["token"] == "legacy-saved"


# ── OAuth2 token_timeout round-trip ──────────────────────────────────────────


class TestOAuth2TokenTimeout:
    def test_token_timeout_survives_round_trip(self):
        auth = OAuth2Auth(
            token_url="https://tok.test/token",
            client_id="c",
            token_timeout=25.0,
        )
        d = auth.to_dict()
        assert d["token_timeout"] == 25.0
        restored = OAuth2Auth.from_dict(d)
        assert restored.token_timeout == 25.0

    def test_default_token_timeout_when_missing(self):
        """from_dict with no token_timeout key uses the class default."""
        d = {"type": "oauth2", "client_id": "c", "token_url": "https://t.test/tok"}
        restored = OAuth2Auth.from_dict(d)
        assert restored.token_timeout == OAuth2Auth.DEFAULT_TOKEN_TIMEOUT

