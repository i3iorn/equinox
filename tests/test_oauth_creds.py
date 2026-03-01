"""Tests for OAuthClientManager and SavedCredentialsManager."""
import pytest
from equinox.storage.database import Database
from equinox.storage.oauth_clients import OAuthClientManager
from equinox.storage.saved_credentials import SavedCredentialsManager
from equinox.core.exceptions import StorageError, ValidationError
@pytest.fixture
def db(tmp_path):
    return Database(str(tmp_path / 'test.db'))
# ── OAuthClientManager ───────────────────────────────────────────────────────
class TestOAuthClientManager:
    def test_create_and_get(self, db):
        mgr = OAuthClientManager(db)
        cid = mgr.create_client(
            name='My Client', token_url='https://auth.example.com/token',
            client_id='cid123', client_secret='sec456', scope='read write',
            grant_type='client_credentials', description='Test client',
        )
        assert cid >= 1
        client = mgr.get_client(cid)
        assert client is not None
        assert client['name'] == 'My Client'
        assert client['client_id'] == 'cid123'
        assert client['grant_type'] == 'client_credentials'
    def test_list_clients(self, db):
        mgr = OAuthClientManager(db)
        mgr.create_client(name='A', token_url='', client_id='', client_secret='')
        mgr.create_client(name='B', token_url='', client_id='', client_secret='')
        clients = mgr.list_clients()
        assert len(clients) == 2
    def test_duplicate_name_raises(self, db):
        mgr = OAuthClientManager(db)
        mgr.create_client(name='Dup', token_url='', client_id='', client_secret='')
        with pytest.raises(StorageError, match='already exists'):
            mgr.create_client(name='Dup', token_url='', client_id='', client_secret='')
    def test_update_client(self, db):
        mgr = OAuthClientManager(db)
        cid = mgr.create_client(name='Old', token_url='', client_id='', client_secret='')
        mgr.update_client(cid, name='New')
        client = mgr.get_client(cid)
        assert client['name'] == 'New'
    def test_delete_client(self, db):
        mgr = OAuthClientManager(db)
        cid = mgr.create_client(name='ToDelete', token_url='', client_id='', client_secret='')
        mgr.delete_client(cid)
        assert mgr.get_client(cid) is None
    def test_set_default(self, db):
        mgr = OAuthClientManager(db)
        c1 = mgr.create_client(name='C1', token_url='', client_id='', client_secret='')
        c2 = mgr.create_client(name='C2', token_url='', client_id='', client_secret='')
        mgr.set_default(c1)
        assert mgr.get_default()['id'] == c1
        mgr.set_default(c2)
        assert mgr.get_default()['id'] == c2
    def test_invalid_grant_type(self, db):
        mgr = OAuthClientManager(db)
        with pytest.raises(ValidationError):
            mgr.create_client(name='Bad', token_url='', client_id='', client_secret='',
                              grant_type='invalid_grant')
    def test_get_nonexistent(self, db):
        mgr = OAuthClientManager(db)
        assert mgr.get_client(9999) is None
# ── SavedCredentialsManager ──────────────────────────────────────────────────
class TestSavedCredentialsManager:
    def test_create_bearer(self, db):
        mgr = SavedCredentialsManager(db)
        cid = mgr.create_credential(
            name='My Token', auth_type='bearer',
            config={'token': 'abc123'}, description='Test bearer',
        )
        assert cid >= 1
        cred = mgr.get_credential(cid)
        assert cred['name'] == 'My Token'
        assert cred['auth_type'] == 'bearer'
        assert cred['config']['token'] == 'abc123'
    def test_create_basic(self, db):
        mgr = SavedCredentialsManager(db)
        cid = mgr.create_credential(
            name='Basic Cred', auth_type='basic',
            config={'username': 'user', 'password': 'pass'},
        )
        cred = mgr.get_credential(cid)
        assert cred['config']['username'] == 'user'
    def test_create_api_key(self, db):
        mgr = SavedCredentialsManager(db)
        cid = mgr.create_credential(
            name='API Key', auth_type='api_key',
            config={'key': 'X-API-Key', 'value': 'secret', 'location': 'header'},
        )
        cred = mgr.get_credential(cid)
        assert cred['config']['location'] == 'header'
    def test_list_and_delete(self, db):
        mgr = SavedCredentialsManager(db)
        mgr.create_credential(name='C1', auth_type='bearer', config={'token': 'a'})
        mgr.create_credential(name='C2', auth_type='bearer', config={'token': 'b'})
        assert len(mgr.list_credentials()) == 2
        creds = mgr.list_credentials()
        mgr.delete_credential(creds[0]['id'])
        assert len(mgr.list_credentials()) == 1
    def test_update_credential(self, db):
        mgr = SavedCredentialsManager(db)
        cid = mgr.create_credential(name='Old', auth_type='bearer', config={'token': 'old'})
        mgr.update_credential(cid, config={'token': 'new'})
        cred = mgr.get_credential(cid)
        assert cred['config']['token'] == 'new'
    def test_duplicate_name_raises(self, db):
        mgr = SavedCredentialsManager(db)
        mgr.create_credential(name='Dup', auth_type='bearer', config={'token': 'x'})
        with pytest.raises(StorageError):
            mgr.create_credential(name='Dup', auth_type='bearer', config={'token': 'y'})
    def test_invalid_auth_type(self, db):
        mgr = SavedCredentialsManager(db)
        with pytest.raises(ValidationError):
            mgr.create_credential(name='Bad', auth_type='unknown', config={})
