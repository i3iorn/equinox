"""Tests for OAuth2 scope handling across all grant flows.

Verifies that the ``scope`` parameter is correctly included (or omitted)
in token-endpoint requests for both client_credentials and refresh_token
flows, round-trips through serialization, and propagates from storage layers.
"""

import pytest
from unittest.mock import Mock, MagicMock, patch
from datetime import datetime, timedelta, timezone

from equinox.auth.oauth2 import OAuth2Auth


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_token_response(
    access_token="new-token",
    expires_in=3600,
    refresh_token=None,
):
    """Return a Mock httpx response for a token endpoint."""
    body = {
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": expires_in,
    }
    if refresh_token:
        body["refresh_token"] = refresh_token
    resp = Mock()
    resp.status_code = 200
    resp.json.return_value = body
    resp.raise_for_status = Mock()
    return resp


# ---------------------------------------------------------------------------
# Client-credentials flow – scope handling
# ---------------------------------------------------------------------------

class TestClientCredentialsScopeHandling:
    """Scope must be sent when configured for client_credentials."""

    @patch("equinox.auth.oauth2.httpx.Client")
    def test_scope_included_in_client_credentials(self, mock_client_class):
        """Scope should appear in the POST body for client_credentials."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        mock_client.post.return_value = _mock_token_response()

        auth = OAuth2Auth(
            client_id="cid",
            client_secret="csec",
            token_url="https://auth.example.com/token",
            scope="read write",
        )
        auth.apply(Mock(), {})

        mock_client.post.assert_called_once()
        sent_data = mock_client.post.call_args[1].get("data") or mock_client.post.call_args[0][1] if len(mock_client.post.call_args[0]) > 1 else mock_client.post.call_args[1]["data"]
        assert sent_data["grant_type"] == "client_credentials"
        assert sent_data["scope"] == "read write"

    @patch("equinox.auth.oauth2.httpx.Client")
    def test_scope_omitted_when_none_client_credentials(self, mock_client_class):
        """When scope is None, the key should not be in the POST body."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        mock_client.post.return_value = _mock_token_response()

        auth = OAuth2Auth(
            client_id="cid",
            client_secret="csec",
            token_url="https://auth.example.com/token",
            scope=None,
        )
        auth.apply(Mock(), {})

        sent_data = mock_client.post.call_args[1].get("data") or mock_client.post.call_args[0][1]
        assert "scope" not in sent_data

    @patch("equinox.auth.oauth2.httpx.Client")
    def test_scope_omitted_when_empty_string_client_credentials(self, mock_client_class):
        """An empty-string scope should be treated as absent."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        mock_client.post.return_value = _mock_token_response()

        auth = OAuth2Auth(
            client_id="cid",
            client_secret="csec",
            token_url="https://auth.example.com/token",
            scope="",
        )
        auth.apply(Mock(), {})

        sent_data = mock_client.post.call_args[1].get("data") or mock_client.post.call_args[0][1]
        assert "scope" not in sent_data

    @patch("equinox.auth.oauth2.httpx.Client")
    def test_single_scope_client_credentials(self, mock_client_class):
        """A single scope value should be sent correctly."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        mock_client.post.return_value = _mock_token_response()

        auth = OAuth2Auth(
            client_id="cid",
            client_secret="csec",
            token_url="https://auth.example.com/token",
            scope="openid",
        )
        auth.apply(Mock(), {})

        sent_data = mock_client.post.call_args[1].get("data") or mock_client.post.call_args[0][1]
        assert sent_data["scope"] == "openid"

    @patch("equinox.auth.oauth2.httpx.Client")
    def test_multiple_scopes_client_credentials(self, mock_client_class):
        """Multiple space-separated scopes should be sent as-is."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        mock_client.post.return_value = _mock_token_response()

        auth = OAuth2Auth(
            client_id="cid",
            client_secret="csec",
            token_url="https://auth.example.com/token",
            scope="openid profile email",
        )
        auth.apply(Mock(), {})

        sent_data = mock_client.post.call_args[1].get("data") or mock_client.post.call_args[0][1]
        assert sent_data["scope"] == "openid profile email"


# ---------------------------------------------------------------------------
# Refresh-token flow – scope handling
# ---------------------------------------------------------------------------

class TestRefreshTokenScopeHandling:
    """Scope must be sent when configured for refresh_token flow (RFC 6749 §6)."""

    @patch("equinox.auth.oauth2.httpx.Client")
    def test_scope_included_in_refresh_token_flow(self, mock_client_class):
        """Scope should appear in the POST body for refresh_token flow."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        mock_client.post.return_value = _mock_token_response(refresh_token="new-rt")

        auth = OAuth2Auth(
            client_id="cid",
            client_secret="csec",
            token_url="https://auth.example.com/token",
            scope="read write",
            refresh_token="old-rt",
        )
        # No access_token → triggers refresh
        auth.apply(Mock(), {})

        mock_client.post.assert_called_once()
        sent_data = mock_client.post.call_args[1].get("data") or mock_client.post.call_args[0][1]
        assert sent_data["grant_type"] == "refresh_token"
        assert sent_data["scope"] == "read write"
        assert sent_data["refresh_token"] == "old-rt"

    @patch("equinox.auth.oauth2.httpx.Client")
    def test_scope_omitted_when_none_refresh_token_flow(self, mock_client_class):
        """When scope is None, the key should not be in the refresh POST body."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        mock_client.post.return_value = _mock_token_response(refresh_token="new-rt")

        auth = OAuth2Auth(
            client_id="cid",
            client_secret="csec",
            token_url="https://auth.example.com/token",
            scope=None,
            refresh_token="old-rt",
        )
        auth.apply(Mock(), {})

        sent_data = mock_client.post.call_args[1].get("data") or mock_client.post.call_args[0][1]
        assert sent_data["grant_type"] == "refresh_token"
        assert "scope" not in sent_data

    @patch("equinox.auth.oauth2.httpx.Client")
    def test_scope_omitted_when_empty_string_refresh_token_flow(self, mock_client_class):
        """Empty-string scope should not be sent for refresh_token flow."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        mock_client.post.return_value = _mock_token_response(refresh_token="new-rt")

        auth = OAuth2Auth(
            client_id="cid",
            client_secret="csec",
            token_url="https://auth.example.com/token",
            scope="",
            refresh_token="old-rt",
        )
        auth.apply(Mock(), {})

        sent_data = mock_client.post.call_args[1].get("data") or mock_client.post.call_args[0][1]
        assert "scope" not in sent_data

    @patch("equinox.auth.oauth2.httpx.Client")
    def test_multiple_scopes_refresh_token_flow(self, mock_client_class):
        """Multiple scopes should be sent correctly for refresh_token flow."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        mock_client.post.return_value = _mock_token_response(refresh_token="new-rt")

        auth = OAuth2Auth(
            client_id="cid",
            client_secret="csec",
            token_url="https://auth.example.com/token",
            scope="openid profile email",
            refresh_token="old-rt",
        )
        auth.apply(Mock(), {})

        sent_data = mock_client.post.call_args[1].get("data") or mock_client.post.call_args[0][1]
        assert sent_data["scope"] == "openid profile email"


# ---------------------------------------------------------------------------
# Scope preserved across token refreshes
# ---------------------------------------------------------------------------

class TestScopePreservedAcrossRefreshes:
    """Scope must stay on the instance and be sent on every subsequent refresh."""

    @patch("equinox.auth.oauth2.httpx.Client")
    def test_scope_persists_through_two_refreshes(self, mock_client_class):
        """After a first refresh, a second should still send scope."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        mock_client.post.return_value = _mock_token_response(expires_in=1)

        auth = OAuth2Auth(
            client_id="cid",
            client_secret="csec",
            token_url="https://auth.example.com/token",
            scope="admin",
        )

        # First request → token fetched
        auth.apply(Mock(), {})
        first_data = mock_client.post.call_args[1].get("data") or mock_client.post.call_args[0][1]
        assert first_data["scope"] == "admin"

        # Expire the token to force a second refresh
        auth.expires_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=60)

        auth.apply(Mock(), {})
        second_data = mock_client.post.call_args[1].get("data") or mock_client.post.call_args[0][1]
        assert second_data["scope"] == "admin"
        assert mock_client.post.call_count == 2

    @patch("equinox.auth.oauth2.httpx.Client")
    def test_scope_persists_when_switching_to_refresh_token_flow(self, mock_client_class):
        """Token endpoint returns a refresh_token; next call should still send scope."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        
        # First call: client_credentials returns a refresh_token
        mock_client.post.return_value = _mock_token_response(
            access_token="tok1", expires_in=1, refresh_token="rt1"
        )

        auth = OAuth2Auth(
            client_id="cid",
            client_secret="csec",
            token_url="https://auth.example.com/token",
            scope="read",
        )

        auth.apply(Mock(), {})
        first_data = mock_client.post.call_args[1].get("data") or mock_client.post.call_args[0][1]
        assert first_data["grant_type"] == "client_credentials"
        assert first_data["scope"] == "read"

        # Now auth has a refresh_token; expire token to force refresh
        auth.expires_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=60)

        mock_client.post.return_value = _mock_token_response(
            access_token="tok2", expires_in=3600, refresh_token="rt2"
        )

        auth.apply(Mock(), {})
        second_data = mock_client.post.call_args[1].get("data") or mock_client.post.call_args[0][1]
        assert second_data["grant_type"] == "refresh_token"
        assert second_data["scope"] == "read"


# ---------------------------------------------------------------------------
# Serialization round-trip preserves scope
# ---------------------------------------------------------------------------

class TestScopeSerializationRoundTrip:
    """Scope must survive to_dict → from_dict round trips."""

    def test_to_dict_includes_scope(self):
        auth = OAuth2Auth(
            client_id="cid",
            client_secret="csec",
            token_url="https://auth.example.com/token",
            scope="read write",
        )
        d = auth.to_dict()
        assert d["scope"] == "read write"

    def test_to_dict_scope_is_none_when_unset(self):
        auth = OAuth2Auth(
            client_id="cid",
            client_secret="csec",
            token_url="https://auth.example.com/token",
        )
        d = auth.to_dict()
        assert d["scope"] is None

    def test_from_dict_restores_scope(self):
        data = {
            "type": "oauth2",
            "client_id": "cid",
            "client_secret": "csec",
            "token_url": "https://auth.example.com/token",
            "scope": "openid profile",
        }
        auth = OAuth2Auth.from_dict(data)
        assert auth.scope == "openid profile"

    def test_from_dict_scope_none_when_missing(self):
        data = {
            "type": "oauth2",
            "client_id": "cid",
            "client_secret": "csec",
            "token_url": "https://auth.example.com/token",
        }
        auth = OAuth2Auth.from_dict(data)
        assert auth.scope is None

    def test_round_trip_preserves_scope(self):
        original = OAuth2Auth(
            client_id="cid",
            client_secret="csec",
            token_url="https://auth.example.com/token",
            scope="a b c",
        )
        restored = OAuth2Auth.from_dict(original.to_dict())
        assert restored.scope == "a b c"

    def test_round_trip_preserves_none_scope(self):
        original = OAuth2Auth(
            client_id="cid",
            client_secret="csec",
            token_url="https://auth.example.com/token",
            scope=None,
        )
        restored = OAuth2Auth.from_dict(original.to_dict())
        assert restored.scope is None


# ---------------------------------------------------------------------------
# OAuthClientManager → OAuth2Auth scope propagation
# ---------------------------------------------------------------------------

class TestOAuthClientManagerScopePropagation:
    """Ensure scope set in the DB flows through to_oauth2_auth → actual request."""

    @pytest.fixture
    def db(self, tmp_path):
        from equinox.storage.database import Database
        return Database(str(tmp_path / "test.db"))

    @pytest.fixture
    def mgr(self, db):
        from equinox.storage.oauth_clients import OAuthClientManager
        return OAuthClientManager(db)

    def test_to_oauth2_auth_passes_scope(self, mgr):
        cid = mgr.create_client(
            name="Scoped Client",
            token_url="https://auth.example.com/token",
            client_id="cid",
            client_secret="csec",
            scope="read write",
        )
        client = mgr.get_client(cid)
        auth = mgr.to_oauth2_auth(client)

        assert auth.scope == "read write"

    def test_to_oauth2_auth_scope_none_when_empty(self, mgr):
        cid = mgr.create_client(
            name="No Scope Client",
            token_url="https://auth.example.com/token",
            client_id="cid",
            client_secret="csec",
            scope="",
        )
        client = mgr.get_client(cid)
        auth = mgr.to_oauth2_auth(client)

        # Empty string should become None (falsy values normalised)
        assert auth.scope is None

    @patch("equinox.auth.oauth2.httpx.Client")
    def test_scope_reaches_token_endpoint_via_manager(self, mock_client_class, mgr):
        """End-to-end: DB scope value → OAuth2Auth → token endpoint POST body."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        mock_client.post.return_value = _mock_token_response()

        cid = mgr.create_client(
            name="E2E Client",
            token_url="https://auth.example.com/token",
            client_id="cid",
            client_secret="csec",
            scope="api.read api.write",
        )
        client = mgr.get_client(cid)
        auth = mgr.to_oauth2_auth(client)
        auth.apply(Mock(), {})

        sent_data = mock_client.post.call_args[1].get("data") or mock_client.post.call_args[0][1]
        assert sent_data["scope"] == "api.read api.write"


# ---------------------------------------------------------------------------
# SavedCredentialsManager → OAuth2Auth scope propagation
# ---------------------------------------------------------------------------

class TestSavedCredentialsScopePropagation:
    """Ensure scope in saved_credentials config flows to OAuth2Auth."""

    @pytest.fixture
    def db(self, tmp_path):
        from equinox.storage.database import Database
        return Database(str(tmp_path / "test.db"))

    @pytest.fixture
    def mgr(self, db):
        from equinox.storage.saved_credentials import SavedCredentialsManager
        return SavedCredentialsManager(db)

    def test_saved_credential_scope_propagates(self, mgr):
        cid = mgr.create_credential(
            name="OAuth2 Cred",
            auth_type="oauth2",
            config={
                "token_url": "https://auth.example.com/token",
                "client_id": "cid",
                "client_secret": "csec",
                "scope": "admin",
            },
        )
        cred = mgr.get_credential(cid)
        auth = mgr.to_auth_strategy(cred)

        assert auth.scope == "admin"

    def test_saved_credential_no_scope(self, mgr):
        cid = mgr.create_credential(
            name="OAuth2 No Scope",
            auth_type="oauth2",
            config={
                "token_url": "https://auth.example.com/token",
                "client_id": "cid",
                "client_secret": "csec",
            },
        )
        cred = mgr.get_credential(cid)
        auth = mgr.to_auth_strategy(cred)

        assert auth.scope is None

    @patch("equinox.auth.oauth2.httpx.Client")
    def test_saved_credential_scope_reaches_token_endpoint(self, mock_client_class, mgr):
        """End-to-end: saved_credentials scope → OAuth2Auth → POST body."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        mock_client.post.return_value = _mock_token_response()

        cid = mgr.create_credential(
            name="Full E2E",
            auth_type="oauth2",
            config={
                "token_url": "https://auth.example.com/token",
                "client_id": "cid",
                "client_secret": "csec",
                "scope": "read write delete",
            },
        )
        cred = mgr.get_credential(cid)
        auth = mgr.to_auth_strategy(cred)
        auth.apply(Mock(), {})

        sent_data = mock_client.post.call_args[1].get("data") or mock_client.post.call_args[0][1]
        assert sent_data["scope"] == "read write delete"

