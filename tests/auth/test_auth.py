"""Tests for authentication modules."""

import pytest
from unittest.mock import Mock, MagicMock, patch
import base64

from equinox.auth import (
    BearerAuth, BasicAuth, APIKeyAuth, OAuth2Auth
)
from equinox.core import AuthError
from equinox.core.request import Request


class TestBearerAuth:
    """Tests for Bearer authentication."""

    def test_bearer_auth_initialization(self):
        """Test Bearer auth initialization."""
        auth = BearerAuth("test-token-123")
        assert auth.token == "test-token-123"

    def test_bearer_auth_apply(self):
        """Test applying Bearer auth to request."""
        auth = BearerAuth("my-token")
        request = Mock()
        headers = {}

        auth.apply(request, headers)

        assert "Authorization" in headers
        assert headers["Authorization"] == "Bearer my-token"

    def test_bearer_auth_with_empty_token(self):
        """Test Bearer auth rejects empty token."""
        from equinox.core.exceptions import AuthError
        with pytest.raises(AuthError, match="non-empty string"):
            BearerAuth("")


class TestAPIKeyAuth:
    """Tests for API Key authentication."""

    def test_api_key_auth_header(self):
        """Test API key in header."""
        auth = APIKeyAuth("X-API-Key", "secret-key-123", location="header")
        request = Mock()
        headers = {}

        auth.apply(request, headers)

        assert "X-API-Key" in headers
        assert headers["X-API-Key"] == "secret-key-123"

    def test_api_key_auth_query(self):
        """Test API key in query parameter."""
        auth = APIKeyAuth("api_key", "secret-key-123", location="query")
        request = Request(method="GET", url="https://api.example.com")
        headers = {}

        auth.apply(request, headers)

        assert "api_key" in request.params
        assert request.params["api_key"] == "secret-key-123"

    def test_api_key_auth_invalid_location(self):
        """Test API key with invalid location."""
        with pytest.raises(AuthError, match="location"):
            APIKeyAuth("key", "value", location="invalid")


class TestBasicAuth:
    """Tests for Basic authentication."""

    def test_basic_auth_initialization(self):
        """Test Basic auth initialization."""
        auth = BasicAuth("username", "password")
        assert auth.username == "username"
        assert auth.password == "password"

    def test_basic_auth_apply(self):
        """Test applying Basic auth to request."""
        auth = BasicAuth("user", "pass")
        request = Mock()
        headers = {}

        auth.apply(request, headers)

        assert "Authorization" in headers
        assert headers["Authorization"].startswith("Basic ")

        # Verify encoding
        encoded = headers["Authorization"].split(" ")[1]
        decoded = base64.b64decode(encoded).decode()
        assert decoded == "user:pass"

    def test_basic_auth_special_characters(self):
        """Test Basic auth with special characters."""
        auth = BasicAuth("user@example.com", "p@ssw0rd!")
        request = Mock()
        headers = {}

        auth.apply(request, headers)

        encoded = headers["Authorization"].split(" ")[1]
        decoded = base64.b64decode(encoded).decode()
        assert decoded == "user@example.com:p@ssw0rd!"


class TestOAuth2Auth:
    """Tests for OAuth2 authentication."""

    def test_oauth2_initialization(self):
        """Test OAuth2 initialization."""
        auth = OAuth2Auth(
            client_id="client-123",
            client_secret="secret-456",
            token_url="https://auth.example.com/token"
        )

        assert auth.client_id == "client-123"
        assert auth.client_secret == "secret-456"
        assert auth.token_url == "https://auth.example.com/token"
        assert auth.access_token is None

    def test_oauth2_with_existing_token(self):
        """Test OAuth2 with existing access token."""
        auth = OAuth2Auth(
            client_id="client",
            client_secret="secret",
            token_url="https://auth.example.com/token",
            access_token="existing-token"
        )

        assert auth.access_token == "existing-token"

    @patch('equinox.auth._oauth2.httpx.Client')
    def test_oauth2_request_token(self, mock_client_class):
        """Test requesting OAuth2 token."""
        # Mock the httpx.Client instance and its post method
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "new-token",
            "token_type": "Bearer",
            "expires_in": 3600
        }
        mock_client.post.return_value = mock_response

        auth = OAuth2Auth(
            client_id="client",
            client_secret="secret",
            token_url="https://auth.example.com/token"
        )

        # Trigger token refresh by applying auth
        request = Mock()
        headers = {}
        auth.apply(request, headers)

        assert auth.access_token == "new-token"
        assert headers["Authorization"] == "Bearer new-token"

    @patch('equinox.auth._oauth2.httpx.Client')
    def test_oauth2_apply_with_token(self, mock_client):
        """Test applying OAuth2 auth with existing token."""
        auth = OAuth2Auth(
            client_id="client",
            client_secret="secret",
            token_url="https://auth.example.com/token",
            access_token="my-token"
        )

        request = Mock()
        headers = {}

        auth.apply(request, headers)

        assert "Authorization" in headers
        assert headers["Authorization"] == "Bearer my-token"

    @patch('equinox.auth._oauth2.httpx.Client')
    def test_oauth2_apply_requests_token_if_needed(self, mock_client_class):
        """Test that OAuth2 requests token if not available."""
        # Mock the httpx.Client instance and its post method
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "auto-requested-token",
            "token_type": "Bearer",
            "expires_in": 3600
        }
        mock_client.post.return_value = mock_response

        auth = OAuth2Auth(
            client_id="client",
            client_secret="secret",
            token_url="https://auth.example.com/token"
        )

        request = Mock()
        headers = {}

        auth.apply(request, headers)

        assert auth.access_token == "auto-requested-token"
        assert headers["Authorization"] == "Bearer auto-requested-token"

    @patch('equinox.auth._oauth2.httpx.Client')
    def test_oauth2_token_refresh(self, mock_client_class):
        """Test OAuth2 token refresh."""
        # Mock the httpx.Client instance and its post method
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "refreshed-token",
            "token_type": "Bearer",
            "expires_in": 3600,
            "refresh_token": "new-refresh-token"
        }
        mock_client.post.return_value = mock_response

        auth = OAuth2Auth(
            client_id="client",
            client_secret="secret",
            token_url="https://auth.example.com/token",
            refresh_token="old-refresh-token"
        )

        # Trigger refresh by applying auth (which checks if refresh is needed)
        request = Mock()
        headers = {}
        auth.apply(request, headers)

        assert auth.access_token == "refreshed-token"
        assert auth.refresh_token == "new-refresh-token"

    @patch('equinox.auth._oauth2.httpx.Client')
    def test_oauth2_token_request_failure(self, mock_client):
        """Test OAuth2 token request failure."""
        # Mock failed response
        mock_response = Mock()
        mock_response.status_code = 401
        mock_response.text = "Unauthorized"

        mock_client_instance = Mock()
        mock_client_instance.post.return_value = mock_response
        mock_client.return_value.__enter__.return_value = mock_client_instance

        auth = OAuth2Auth(
            client_id="client",
            client_secret="secret",
            token_url="https://auth.example.com/token"
        )

        with pytest.raises(Exception):
            auth.request_token()


class TestAuthIntegration:
    """Integration tests for authentication."""

    def test_multiple_auth_types(self):
        """Test that different auth types can be used interchangeably."""
        request = Request(method="GET", url="https://api.example.com")

        # Bearer auth
        bearer = BearerAuth("token1")
        headers1 = {}
        bearer.apply(request, headers1)
        assert "Authorization" in headers1

        # API Key auth
        api_key = APIKeyAuth("X-API-Key", "key1", "header")
        headers2 = {}
        api_key.apply(request, headers2)
        assert "X-API-Key" in headers2

        # Basic auth
        basic = BasicAuth("user", "pass")
        headers3 = {}
        basic.apply(request, headers3)
        assert "Authorization" in headers3
        assert headers3["Authorization"].startswith("Basic ")

    def test_auth_headers_dont_conflict(self):
        """Test that auth methods don't conflict."""
        request = Request(method="GET", url="https://api.example.com")

        # Apply API key (header-based)
        api_key = APIKeyAuth("X-Custom-Key", "value", "header")
        headers = {}
        api_key.apply(request, headers)

        # Apply Bearer auth
        bearer = BearerAuth("token")
        bearer.apply(request, headers)

        # Both should be present
        assert "X-Custom-Key" in headers
        assert "Authorization" in headers


class TestAuthStorage:
    """Test auth storage and retrieval"""

    @pytest.fixture
    def db(self):
        """Create temporary database"""
        import tempfile
        from pathlib import Path
        from equinox.storage import Database

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            db_path = tmp.name

        db = Database(db_path)
        yield db

        # Close the connection before cleanup so Windows releases the file lock
        db.close()
        Path(db_path).unlink(missing_ok=True)

    @pytest.fixture
    def mgr(self, db):
        """Create collection manager"""
        from equinox.storage import CollectionManager
        return CollectionManager(db)

    @pytest.fixture
    def collection_id(self, mgr):
        """Create test collection"""
        return mgr.create_collection("Test Collection", "Test")

    def test_save_request_with_basic_auth(self, mgr, collection_id):
        """Test saving request with basic auth"""
        auth = BasicAuth(username="user", password="pass")
        request = Request(
            method="GET",
            url="https://api.example.com/protected",
            auth=auth,
            name="Protected Endpoint"
        )

        req_id = mgr.save_request(request, collection_id=collection_id)
        assert req_id > 0

        # Load and verify
        loaded = mgr.get_request(req_id)
        assert loaded is not None
        assert loaded.auth is not None
        assert isinstance(loaded.auth, BasicAuth)
        assert loaded.auth.username == "user"
        assert loaded.auth.password == "pass"

    def test_save_request_with_bearer_auth(self, mgr, collection_id):
        """Test saving request with bearer token"""
        auth = BearerAuth(token="abc123xyz")
        request = Request(
            method="GET",
            url="https://api.example.com/protected",
            auth=auth,
            name="Bearer Auth Endpoint"
        )

        req_id = mgr.save_request(request, collection_id=collection_id)
        loaded = mgr.get_request(req_id)

        assert loaded.auth is not None
        assert isinstance(loaded.auth, BearerAuth)
        assert loaded.auth.token == "abc123xyz"

    def test_save_request_with_oauth2(self, mgr, collection_id):
        """Test saving request with OAuth2"""
        auth = OAuth2Auth(
            token_url="https://auth.example.com/token",
            client_id="client123",
            client_secret="secret",
            scope="read",
        )
        request = Request(
            method="GET",
            url="https://api.example.com/data",
            auth=auth,
            name="OAuth2 Endpoint"
        )

        req_id = mgr.save_request(request, collection_id=collection_id)
        loaded = mgr.get_request(req_id)

        assert loaded.auth is not None
        assert isinstance(loaded.auth, OAuth2Auth)
        assert loaded.auth.token_url == "https://auth.example.com/token"
        assert loaded.auth.client_id == "client123"
        assert loaded.auth.client_secret == "secret"
        assert loaded.auth.scope == "read"

    def test_save_request_with_api_key(self, mgr, collection_id):
        """Test saving request with API key"""
        auth = APIKeyAuth(key="X-API-Key", value="key123", location="header")
        request = Request(
            method="GET",
            url="https://api.example.com/data",
            auth=auth,
            name="API Key Endpoint"
        )

        req_id = mgr.save_request(request, collection_id=collection_id)
        loaded = mgr.get_request(req_id)

        assert loaded.auth is not None
        assert isinstance(loaded.auth, APIKeyAuth)
        assert loaded.auth.key == "X-API-Key"
        assert loaded.auth.value == "key123"
        assert loaded.auth.location == "header"

    def test_save_request_without_auth(self, mgr, collection_id):
        """Test saving request without auth"""
        request = Request(
            method="GET",
            url="https://api.example.com/public",
            name="Public Endpoint"
        )

        req_id = mgr.save_request(request, collection_id=collection_id)
        loaded = mgr.get_request(req_id)

        assert loaded.auth is None

    def test_load_request_preserves_auth(self, mgr, collection_id):
        """Test that loading a request preserves auth"""
        auth = BasicAuth(username="testuser", password="testpass")
        request = Request(
            method="POST",
            url="https://api.example.com/create",
            auth=auth,
            name="Create Endpoint"
        )

        req_id = mgr.save_request(request, collection_id=collection_id)

        # Load multiple times to verify persistence
        for _ in range(3):
            loaded = mgr.get_request(req_id)
            assert loaded.auth is not None
            assert isinstance(loaded.auth, BasicAuth)
            assert loaded.auth.username == "testuser"
            assert loaded.auth.password == "testpass"


class TestAuthSerialization:
    """Test auth serialization and deserialization"""

    def test_basic_auth_to_dict(self):
        """Test BasicAuth serialization"""
        auth = BasicAuth(username="user", password="pass")
        data = auth.to_dict()

        assert data["type"] == "basic"
        assert data["username"] == "user"
        assert data["password"] == "pass"

    def test_basic_auth_from_dict(self):
        """Test BasicAuth deserialization"""
        data = {"type": "basic", "username": "user", "password": "pass"}
        auth = BasicAuth.from_dict(data)

        assert auth.username == "user"
        assert auth.password == "pass"

    def test_bearer_auth_to_dict(self):
        """Test BearerAuth serialization"""
        auth = BearerAuth(token="abc123")
        data = auth.to_dict()

        assert data["type"] == "bearer"
        assert data["token"] == "abc123"

    def test_bearer_auth_from_dict(self):
        """Test BearerAuth deserialization"""
        data = {"type": "bearer", "token": "abc123"}
        auth = BearerAuth.from_dict(data)

        assert auth.token == "abc123"

    def test_oauth2_auth_to_dict(self):
        """Test OAuth2Auth serialization"""
        auth = OAuth2Auth(
            token_url="https://auth.example.com/token",
            client_id="client123",
            client_secret="secret",
            scope="read write",
            verify_ssl=False,
        )
        data = auth.to_dict()

        assert data["type"] == "oauth2"
        assert data["token_url"] == "https://auth.example.com/token"
        assert data["client_id"] == "client123"
        assert data["client_secret"] == "secret"
        assert data["scope"] == "read write"
        assert data["verify_ssl"] is False

    def test_oauth2_auth_from_dict(self):
        """Test OAuth2Auth deserialization"""
        data = {
            "type": "oauth2",
            "token_url": "https://auth.example.com/token",
            "client_id": "client123",
            "client_secret": "secret",
            "scope": "read write",
            "verify_ssl": False,
        }
        auth = OAuth2Auth.from_dict(data)

        assert auth.token_url == "https://auth.example.com/token"
        assert auth.client_id == "client123"
        assert auth.client_secret == "secret"
        assert auth.scope == "read write"
        assert auth.verify_ssl is False

    def test_api_key_auth_to_dict(self):
        """Test APIKeyAuth serialization"""
        auth = APIKeyAuth(key="X-API-Key", value="key123", location="header")
        data = auth.to_dict()

        assert data["type"] == "api_key"
        assert data["key"] == "X-API-Key"
        assert data["value"] == "key123"
        assert data["location"] == "header"

    def test_api_key_auth_from_dict(self):
        """Test APIKeyAuth deserialization"""
        data = {
            "type": "api_key",
            "key": "X-API-Key",
            "value": "key123",
            "location": "header",
        }
        auth = APIKeyAuth.from_dict(data)

        assert auth.key == "X-API-Key"
        assert auth.value == "key123"
        assert auth.location == "header"
