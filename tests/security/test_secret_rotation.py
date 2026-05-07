import sqlite3
import os
from pathlib import Path

import pytest

from equinox.storage.database import Database
from equinox.storage.oauth_clients import OAuthClientManager
from equinox.security.secrets_password import rotate_all_secrets


def _init_db(tmp_path: Path) -> str:
    db_path = tmp_path / "rotation_test.db"
    # Create database and run migrations
    Database(str(db_path))
    return str(db_path)


def _insert_plain_secret(db_path: str) -> int:
    with sqlite3.connect(db_path) as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO oauth_clients (name, token_url, client_id, client_secret, scope, grant_type, extra_params, description) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("PlainSecret", "https://example/token", "cid", "secret-plaintext", "", "client_credentials", '{}', ''),
        )
        conn.commit()
        return cur.lastrowid


def test_rotation_encrypts_plaintext_secret(tmp_path):
    db_path = _init_db(tmp_path)
    # insert a plaintext client_secret
    client_id = _insert_plain_secret(db_path)
    # rotate to a new master password
    rotate_all_secrets(db_path, new_password="NewMasterPassword123!")
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT id, client_secret FROM oauth_clients WHERE id=?", (client_id,)).fetchone()
        assert row is not None
        assert isinstance(row[1], str)
        assert row[1].startswith("enc:")


def test_rotation_keeps_enc_blobs_unchanged(tmp_path):
    db_path = _init_db(tmp_path)
    client_id = _insert_plain_secret(db_path)
    # First rotation to encrypt plaintext
    rotate_all_secrets(db_path, new_password="FirstPass")
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT id, client_secret FROM oauth_clients WHERE id=?", (client_id,)).fetchone()
        assert row[1].startswith("enc:")
        first_enc = row[1]
    # Rotate again with a different password; enc blob should remain unchanged
    rotate_all_secrets(db_path, new_password="SecondPass")
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT id, client_secret FROM oauth_clients WHERE id=?", (client_id,)).fetchone()
        assert row[1] == first_enc


def test_extra_params_flow_in_oauth2_auth(tmp_path):
    db_path = _init_db(tmp_path)
    with sqlite3.connect(db_path) as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO oauth_clients (name, token_url, client_id, client_secret, scope, grant_type, extra_params, description) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("WithExtra", "https://example/token", "cid2", "sec2", "", "client_credentials", '{"foo":"bar"}', ''),
        )
        conn.commit()
        client_id = cur.lastrowid
        row = conn.execute("SELECT * FROM oauth_clients WHERE id=?", (client_id,)).fetchone()

    from equinox.storage.oauth_clients import OAuthClientManager
    mgr = OAuthClientManager(Database(db_path))
    client = mgr.get_client(client_id)
    auth = mgr.to_oauth2_auth(client)
    assert auth.extra_params == {"foo": "bar"}
    d = auth.to_dict()
    assert d.get("extra_params") == {"foo": "bar"}
    from equinox.auth._oauth2 import OAuth2Auth
    new_auth = OAuth2Auth.from_dict(d)
    assert new_auth.extra_params == {"foo": "bar"}
