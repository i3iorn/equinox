"""Targeted tests to increase coverage for under-tested modules.

Covers:
- auth/oauth2.py: build_grant_data, apply_token_response, storage round-trip,
  parse_expires_at, get_token_info, repr, error paths
- cli/collections.py: collection run, export, requests, variable sub-commands
- cli/history.py: history export (json/csv/har), search, status parsing helpers
- cli/http.py: _prepare_body, _parse_auth, _print_response variants,
  _run_assertions, --save, --format json, --save-response
- core/client.py: timeout clamp, rate limit, concurrent limit, retry logic,
  SSL context, context manager, convenience methods, parse_retry_after,
  _apply_auth, _validate_request, cookie update
- importers/openapi.py: _validate_file, _validate_spec edge cases,
  _parse_operation, _generate_example_from_schema, _resolve_schema_type,
  webhooks, _get_parameter_example
- storage/collections.py: rename_collection, rename_request, duplicate_request,
  folders CRUD, variable management, update_request_auth
"""

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

import pytest
from click.testing import CliRunner

from equinox.storage.database import Database
from equinox.storage.collections import CollectionManager
from equinox.core.request import Request, Response
from equinox.core.exceptions import (
    ValidationError, StorageError, RequestError, RateLimitError,
    TimeoutError as EqTimeoutError, AuthError,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def db(tmp_path):
    return Database(str(tmp_path / "test.db"))


@pytest.fixture
def mgr(db):
    return CollectionManager(db)


@pytest.fixture
def col_id(mgr):
    return mgr.create_collection("Test API", "desc")


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def temp_db(tmp_path):
    db_path = str(tmp_path / "test.db")
    with patch.dict(os.environ, {"EQUINOX_DB_PATH": db_path}):
        yield db_path


def _mock_response(status=200, body=b'{"ok":true}', method="GET",
                   url="https://example.com", headers=None):
    req = Request(method=method, url=url)
    return Response(
        status_code=status,
        reason="OK" if status < 400 else "Error",
        headers=headers or {"content-type": "application/json"},
        body=body,
        elapsed=0.05,
        request=req,
    )


# ═════════════════════════════════════════════════════════════════════════════
# auth/oauth2.py
# ═════════════════════════════════════════════════════════════════════════════

class TestOAuth2Coverage:

    def test_repr_with_long_token(self):
        from equinox.auth.oauth2 import OAuth2Auth
        auth = OAuth2Auth(client_id="cid", access_token="a" * 20)
        r = repr(auth)
        assert "aaaaaaaa..." in r

    def test_repr_without_token(self):
        from equinox.auth.oauth2 import OAuth2Auth
        auth = OAuth2Auth(client_id="cid")
        assert "None" in repr(auth)

    def test_get_token_info(self):
        from equinox.auth.oauth2 import OAuth2Auth
        auth = OAuth2Auth(client_id="cid", access_token="tok12345678",
                          refresh_token="rt")
        info = auth.get_token_info()
        assert info["has_refresh_token"] is True
        assert info["needs_refresh"] is False  # has token, no expiry

    def test_get_token_info_no_token(self):
        from equinox.auth.oauth2 import OAuth2Auth
        auth = OAuth2Auth(client_id="cid")
        info = auth.get_token_info()
        assert info["needs_refresh"] is True
        assert info["access_token"] == "None"

    def test_parse_expires_at_valid_iso(self):
        from equinox.auth.oauth2 import OAuth2Auth
        result = OAuth2Auth._parse_expires_at("2025-06-01T12:00:00")
        assert isinstance(result, datetime)
        assert result.tzinfo is None

    def test_parse_expires_at_with_tz(self):
        from equinox.auth.oauth2 import OAuth2Auth
        result = OAuth2Auth._parse_expires_at("2025-06-01T12:00:00+00:00")
        assert result is not None
        assert result.tzinfo is None  # stripped to naive

    def test_parse_expires_at_invalid(self):
        from equinox.auth.oauth2 import OAuth2Auth
        assert OAuth2Auth._parse_expires_at("not-a-date") is None
        assert OAuth2Auth._parse_expires_at(None) is None
        assert OAuth2Auth._parse_expires_at("") is None

    def test_build_grant_data_refresh_token(self):
        from equinox.auth.oauth2 import OAuth2Auth
        auth = OAuth2Auth(
            token_url="https://x.com/token", client_id="c", client_secret="s",
            refresh_token="rt", scope="read",
        )
        data = auth._build_grant_data()
        assert data["grant_type"] == "refresh_token"
        assert data["refresh_token"] == "rt"
        assert data["scope"] == "read"

    def test_build_grant_data_client_credentials(self):
        from equinox.auth.oauth2 import OAuth2Auth
        auth = OAuth2Auth(
            token_url="https://x.com/token", client_id="c", client_secret="s",
        )
        data = auth._build_grant_data()
        assert data["grant_type"] == "client_credentials"

    def test_build_grant_data_no_credentials(self):
        from equinox.auth.oauth2 import OAuth2Auth
        auth = OAuth2Auth(token_url="https://x.com/token")
        with pytest.raises(AuthError, match="No refresh token"):
            auth._build_grant_data()

    def test_refresh_access_token_no_url(self):
        from equinox.auth.oauth2 import OAuth2Auth
        auth = OAuth2Auth(client_id="c", client_secret="s")
        with pytest.raises(AuthError, match="No token URL"):
            auth._refresh_access_token()

    @patch("equinox.auth.oauth2.httpx.post")
    def test_apply_token_response_no_access_token(self, mock_post):
        from equinox.auth.oauth2 import OAuth2Auth
        mock_resp = Mock()
        mock_resp.json.return_value = {"token_type": "Bearer"}
        mock_resp.raise_for_status = Mock()

        auth = OAuth2Auth(token_url="https://x.com/token", client_id="c", client_secret="s")
        with pytest.raises(AuthError, match="did not return access_token"):
            auth._apply_token_response(mock_resp)

    @patch("equinox.auth.oauth2.httpx.post")
    def test_apply_token_response_invalid_json(self, mock_post):
        from equinox.auth.oauth2 import OAuth2Auth
        mock_resp = Mock()
        mock_resp.json.side_effect = ValueError("bad json")

        auth = OAuth2Auth(token_url="https://x.com/token", client_id="c", client_secret="s")
        with pytest.raises(AuthError, match="Invalid token endpoint"):
            auth._apply_token_response(mock_resp)

    @patch("equinox.auth.oauth2.httpx.post")
    def test_apply_token_response_with_refresh_token(self, mock_post):
        from equinox.auth.oauth2 import OAuth2Auth
        mock_resp = Mock()
        mock_resp.json.return_value = {
            "access_token": "new",
            "expires_in": 7200,
            "refresh_token": "new-rt",
        }
        mock_resp.raise_for_status = Mock()
        auth = OAuth2Auth(token_url="https://x.com/token", client_id="c", client_secret="s")
        auth._apply_token_response(mock_resp)
        assert auth.access_token == "new"
        assert auth.refresh_token == "new-rt"
        assert auth.expires_at is not None

    @patch("equinox.auth.oauth2.httpx.post")
    def test_apply_token_response_default_expiry(self, mock_post):
        from equinox.auth.oauth2 import OAuth2Auth
        mock_resp = Mock()
        mock_resp.json.return_value = {"access_token": "tok"}
        mock_resp.raise_for_status = Mock()
        auth = OAuth2Auth(token_url="https://x.com/token", client_id="c", client_secret="s")
        auth._apply_token_response(mock_resp)
        assert auth.expires_at is not None  # default expiry set

    def test_needs_refresh_expiring_soon(self):
        from equinox.auth.oauth2 import OAuth2Auth
        auth = OAuth2Auth(client_id="c", access_token="tok")
        auth.expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=5)
        assert auth._needs_refresh() is True  # within 30s buffer

    def test_needs_refresh_not_expiring(self):
        from equinox.auth.oauth2 import OAuth2Auth
        auth = OAuth2Auth(client_id="c", access_token="tok")
        auth.expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)
        assert auth._needs_refresh() is False

    def test_needs_refresh_tz_aware_expires_at(self):
        from equinox.auth.oauth2 import OAuth2Auth
        auth = OAuth2Auth(client_id="c", access_token="tok")
        auth.expires_at = datetime.now(timezone.utc) + timedelta(seconds=5)
        assert auth._needs_refresh() is True

    def test_load_from_storage(self):
        from equinox.auth.oauth2 import OAuth2Auth
        mock_storage = Mock()
        mock_storage.retrieve.return_value = json.dumps({
            "access_token": "stored-tok",
            "refresh_token": "stored-rt",
            "expires_at": "2030-01-01T00:00:00",
        })
        auth = OAuth2Auth(
            client_id="c", client_secret="s",
            secure_storage=mock_storage, storage_key="test-key",
        )
        assert auth.access_token == "stored-tok"
        assert auth.refresh_token == "stored-rt"

    def test_load_from_storage_failure(self):
        from equinox.auth.oauth2 import OAuth2Auth
        mock_storage = Mock()
        mock_storage.retrieve.side_effect = Exception("disk error")
        # Should not raise — just logs warning
        auth = OAuth2Auth(client_id="c", secure_storage=mock_storage, storage_key="k")
        assert auth.access_token is None

    def test_save_to_storage(self):
        from equinox.auth.oauth2 import OAuth2Auth
        mock_storage = Mock()
        mock_storage.retrieve.return_value = None
        auth = OAuth2Auth(
            client_id="c", access_token="tok",
            secure_storage=mock_storage, storage_key="k",
        )
        auth._save_to_storage()
        mock_storage.store.assert_called_once()

    def test_save_to_storage_failure(self):
        from equinox.auth.oauth2 import OAuth2Auth
        mock_storage = Mock()
        mock_storage.retrieve.return_value = None
        mock_storage.store.side_effect = Exception("write error")
        auth = OAuth2Auth(
            client_id="c", access_token="tok",
            secure_storage=mock_storage, storage_key="k",
        )
        # Should not raise
        auth._save_to_storage()

    @patch("equinox.auth.oauth2.httpx.post")
    def test_post_token_request_http_status_error(self, mock_post):
        from equinox.auth.oauth2 import OAuth2Auth
        resp = Mock()
        resp.status_code = 401
        resp.raise_for_status.side_effect = __import__("httpx").HTTPStatusError(
            "Unauthorized", request=Mock(), response=resp,
        )
        mock_post.return_value = resp
        auth = OAuth2Auth(
            token_url="https://auth.example.com/token",
            client_id="c", client_secret="s",
        )
        with pytest.raises(AuthError, match="HTTP 401"):
            auth._post_token_request({"grant_type": "client_credentials"})

    def test_to_dict_and_from_dict_round_trip(self):
        from equinox.auth.oauth2 import OAuth2Auth
        auth = OAuth2Auth(
            token_url="https://x.com/token", client_id="c", client_secret="s",
            scope="read", access_token="tok", refresh_token="rt",
        )
        auth.expires_at = datetime(2030, 1, 1, 0, 0, 0)
        d = auth.to_dict()
        restored = OAuth2Auth.from_dict(d)
        assert restored.token_url == auth.token_url
        assert restored.client_id == auth.client_id
        assert restored.client_secret == auth.client_secret
        assert restored.access_token == auth.access_token
        assert restored.refresh_token == auth.refresh_token
        assert restored.expires_at == auth.expires_at


# ═════════════════════════════════════════════════════════════════════════════
# core/client.py
# ═════════════════════════════════════════════════════════════════════════════

class TestHTTPClientCoverage:

    def test_timeout_clamped_below_min(self):
        from equinox.core.client import HTTPClient
        client = HTTPClient(timeout=0.01)
        assert client.timeout == HTTPClient.MIN_TIMEOUT

    def test_timeout_clamped_above_max(self):
        from equinox.core.client import HTTPClient
        client = HTTPClient(timeout=9999)
        assert client.timeout == HTTPClient.MAX_TIMEOUT

    def test_invalid_timeout_raises(self):
        from equinox.core.client import HTTPClient
        with pytest.raises(ValidationError):
            HTTPClient(timeout=-1)
        with pytest.raises(ValidationError):
            HTTPClient(timeout="abc")

    def test_rate_limit_exceeded(self):
        from equinox.core.client import HTTPClient
        client = HTTPClient(max_rate_per_minute=1)
        # First call records a timestamp
        client._check_rate_limit()
        # Second should exceed
        with pytest.raises(RateLimitError):
            client._check_rate_limit()

    def test_rate_limit_unlimited(self):
        from equinox.core.client import HTTPClient
        client = HTTPClient(max_rate_per_minute=0)
        # Should not raise
        client._check_rate_limit()

    def test_concurrent_limit(self):
        from equinox.core.client import HTTPClient
        client = HTTPClient(max_concurrent_requests=1)
        client._check_concurrent_limit()  # slot 1 taken
        with pytest.raises(RequestError, match="Too many concurrent"):
            client._check_concurrent_limit()

    def test_release_concurrent_slot(self):
        from equinox.core.client import HTTPClient
        client = HTTPClient(max_concurrent_requests=1)
        client._check_concurrent_limit()
        client._release_concurrent_slot()
        # Should not raise after release
        client._check_concurrent_limit()

    def test_release_slot_never_below_zero(self):
        from equinox.core.client import HTTPClient
        client = HTTPClient()
        client._release_concurrent_slot()  # no active requests
        assert client._active_requests == 0

    def test_ssl_context_enabled(self):
        from equinox.core.client import HTTPClient
        client = HTTPClient(verify_ssl=True)
        ctx = client._build_ssl_context()
        import ssl
        assert isinstance(ctx, ssl.SSLContext)

    def test_ssl_context_disabled(self):
        from equinox.core.client import HTTPClient
        client = HTTPClient(verify_ssl=False)
        assert client._build_ssl_context() is False

    def test_context_manager(self):
        from equinox.core.client import HTTPClient
        client = HTTPClient()
        with client:
            assert client._client is not None
        assert client._client is None

    def test_get_current_cookies_no_manager(self):
        from equinox.core.client import HTTPClient
        client = HTTPClient()
        assert client._get_current_cookies() == {}

    def test_get_current_cookies_with_manager(self):
        from equinox.core.client import HTTPClient
        mock_cm = Mock()
        mock_cm.to_httpx_cookies.return_value = {"session": "abc"}
        client = HTTPClient(cookie_manager=mock_cm)
        assert client._get_current_cookies() == {"session": "abc"}

    def test_parse_retry_after_valid(self):
        from equinox.core.client import HTTPClient
        client = HTTPClient()
        resp = Mock()
        resp.headers = {"retry-after": "5"}
        assert client._parse_retry_after(resp) == 5.0

    def test_parse_retry_after_capped(self):
        from equinox.core.client import HTTPClient
        client = HTTPClient()
        resp = Mock()
        resp.headers = {"retry-after": "999"}
        assert client._parse_retry_after(resp) == client.RETRY_AFTER_CAP_SECONDS

    def test_parse_retry_after_invalid(self):
        from equinox.core.client import HTTPClient
        client = HTTPClient()
        resp = Mock()
        resp.headers = {"retry-after": "invalid"}
        assert client._parse_retry_after(resp) == 1.0

    def test_parse_retry_after_no_headers(self):
        from equinox.core.client import HTTPClient
        client = HTTPClient()
        resp = Mock()
        resp.headers = None
        assert client._parse_retry_after(resp) == 1.0

    def test_apply_auth_none(self):
        from equinox.core.client import HTTPClient
        client = HTTPClient()
        req = Request(method="GET", url="https://example.com")
        headers = {}
        # Should not raise or modify headers
        client._apply_auth(req, headers, None)
        assert "Authorization" not in headers

    def test_apply_auth_explicit(self):
        from equinox.core.client import HTTPClient
        from equinox.auth import BearerAuth
        client = HTTPClient()
        req = Request(method="GET", url="https://example.com")
        headers = {}
        client._apply_auth(req, headers, BearerAuth("tok123"))
        assert headers["Authorization"] == "Bearer tok123"

    def test_apply_auth_from_request(self):
        from equinox.core.client import HTTPClient
        from equinox.auth import BearerAuth
        client = HTTPClient()
        req = Request(method="GET", url="https://example.com",
                      auth=BearerAuth("req-tok"))
        headers = {}
        client._apply_auth(req, headers, None)
        assert headers["Authorization"] == "Bearer req-tok"

    def test_apply_auth_failure(self):
        from equinox.core.client import HTTPClient
        client = HTTPClient()
        bad_auth = Mock()
        bad_auth.apply.side_effect = Exception("boom")
        req = Request(method="GET", url="https://example.com")
        with pytest.raises(RequestError, match="Authentication failed"):
            client._apply_auth(req, {}, bad_auth)

    def test_validate_request_headers(self):
        from equinox.core.client import HTTPClient
        client = HTTPClient()
        req = Request(method="GET", url="https://example.com",
                      headers={"Accept": "application/json"},
                      params={"q": "test"})
        client._validate_request(req)  # should not raise

    def test_update_cookie_jar_no_manager(self):
        from equinox.core.client import HTTPClient
        client = HTTPClient()
        # Should not raise
        client._update_cookie_jar(
            Request(method="GET", url="https://example.com"),
            _mock_response(),
        )

    def test_build_multipart_files_none(self):
        from equinox.core.client import HTTPClient
        client = HTTPClient()
        req = Request(method="POST", url="https://example.com")
        files, handles = client._build_multipart_files(req)
        assert files is None
        assert handles == []

    def test_build_multipart_files_text_field(self):
        from equinox.core.client import HTTPClient
        client = HTTPClient()
        req = Request(method="POST", url="https://example.com")
        req.multipart_data = [{"key": "name", "type": "text", "value": "Alice"}]
        files, handles = client._build_multipart_files(req)
        assert "name" in files
        assert handles == []

    def test_build_multipart_files_missing_file(self):
        from equinox.core.client import HTTPClient
        client = HTTPClient()
        req = Request(method="POST", url="https://example.com")
        req.multipart_data = [{"key": "doc", "type": "file", "value": "/nonexistent/file.txt"}]
        files, handles = client._build_multipart_files(req)
        assert files["doc"] == (None, b"")
        assert handles == []

    def test_build_multipart_files_empty_key_skipped(self):
        from equinox.core.client import HTTPClient
        client = HTTPClient()
        req = Request(method="POST", url="https://example.com")
        req.multipart_data = [{"key": "", "type": "text", "value": "skip"}]
        files, handles = client._build_multipart_files(req)
        assert files == {}


# ═════════════════════════════════════════════════════════════════════════════
# cli/http.py
# ═════════════════════════════════════════════════════════════════════════════

class TestCliHttpHelpers:

    def test_prepare_body_json(self):
        from equinox.cli.http import _prepare_body
        assert _prepare_body(None, '{"a":1}') == '{"a":1}'

    def test_prepare_body_raw(self):
        from equinox.cli.http import _prepare_body
        assert _prepare_body("raw data", None) == "raw data"

    def test_prepare_body_none(self):
        from equinox.cli.http import _prepare_body
        assert _prepare_body(None, None) is None

    def test_prepare_body_from_file(self, tmp_path):
        from equinox.cli.http import _prepare_body
        f = tmp_path / "body.txt"
        f.write_text("file content", encoding="utf-8")
        result = _prepare_body(f"@{f}", None)
        assert result == "file content"

    def test_prepare_body_file_not_found(self, tmp_path):
        from equinox.cli.http import _prepare_body
        import click
        with pytest.raises(click.BadParameter, match="File not found"):
            _prepare_body(f"@{tmp_path / 'nope.txt'}", None)

    def test_parse_auth_bearer(self):
        from equinox.cli.http import _parse_auth
        from equinox.auth import BearerAuth
        auth = _parse_auth("bearer:my-token")
        assert isinstance(auth, BearerAuth)
        assert auth.token == "my-token"

    def test_parse_auth_basic(self):
        from equinox.cli.http import _parse_auth
        from equinox.auth import BasicAuth
        auth = _parse_auth("basic:user:pass")
        assert isinstance(auth, BasicAuth)
        assert auth.username == "user"
        assert auth.password == "pass"

    def test_parse_auth_apikey(self):
        from equinox.cli.http import _parse_auth
        from equinox.auth import APIKeyAuth
        auth = _parse_auth("apikey:header:X-Key:val123")
        assert isinstance(auth, APIKeyAuth)
        assert auth.location == "header"
        assert auth.key == "X-Key"
        assert auth.value == "val123"

    def test_parse_auth_invalid(self):
        from equinox.cli.http import _parse_auth
        import click
        with pytest.raises(click.BadParameter, match="Invalid auth"):
            _parse_auth("unknown:stuff")


class TestCliHttpCommands:

    @patch("equinox.core.client.HTTPClient.send")
    def test_get_with_headers_and_params(self, mock_send, runner, temp_db):
        from equinox.cli.main import cli
        mock_send.return_value = _mock_response()
        result = runner.invoke(cli, [
            "get", "https://example.com",
            "-H", "Accept: application/json",
            "-p", "page=1",
        ])
        assert result.exit_code == 0

    @patch("equinox.core.client.HTTPClient.send")
    def test_get_with_auth(self, mock_send, runner, temp_db):
        from equinox.cli.main import cli
        mock_send.return_value = _mock_response()
        result = runner.invoke(cli, [
            "get", "https://example.com",
            "--auth", "bearer:my-token",
        ])
        assert result.exit_code == 0

    @patch("equinox.core.client.HTTPClient.send")
    def test_get_json_format(self, mock_send, runner, temp_db):
        from equinox.cli.main import cli
        mock_send.return_value = _mock_response()
        result = runner.invoke(cli, [
            "get", "https://example.com", "--format", "json",
        ])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert "request" in parsed
        assert "response" in parsed

    @patch("equinox.core.client.HTTPClient.send")
    def test_get_save_response(self, mock_send, runner, temp_db, tmp_path):
        from equinox.cli.main import cli
        mock_send.return_value = _mock_response(body=b"saved body")
        out = str(tmp_path / "resp.txt")
        result = runner.invoke(cli, [
            "get", "https://example.com", "--save-response", out,
        ])
        assert result.exit_code == 0
        assert Path(out).read_text(encoding="utf-8") == "saved body"

    @patch("equinox.core.client.HTTPClient.send")
    def test_get_save_to_collection(self, mock_send, runner, temp_db):
        from equinox.cli.main import cli
        mock_send.return_value = _mock_response()
        result = runner.invoke(cli, [
            "get", "https://example.com", "--save", "My Request",
        ])
        assert result.exit_code == 0
        assert "saved" in result.output.lower() or "✓" in result.output

    @patch("equinox.core.client.HTTPClient.send")
    def test_post_with_data(self, mock_send, runner, temp_db):
        from equinox.cli.main import cli
        mock_send.return_value = _mock_response(status=201, method="POST")
        result = runner.invoke(cli, [
            "post", "https://example.com", "--data", "raw body data",
        ])
        assert result.exit_code == 0

    @patch("equinox.core.client.HTTPClient.send")
    def test_assert_status_pass(self, mock_send, runner, temp_db):
        from equinox.cli.main import cli
        mock_send.return_value = _mock_response(status=200)
        result = runner.invoke(cli, [
            "get", "https://example.com", "--assert-status", "200",
        ])
        assert result.exit_code == 0

    @patch("equinox.core.client.HTTPClient.send")
    def test_assert_contains_pass(self, mock_send, runner, temp_db):
        from equinox.cli.main import cli
        mock_send.return_value = _mock_response(body=b'{"ok": true}')
        result = runner.invoke(cli, [
            "get", "https://example.com", "--assert-contains", "ok",
        ])
        assert result.exit_code == 0


# ═════════════════════════════════════════════════════════════════════════════
# cli/collections.py
# ═════════════════════════════════════════════════════════════════════════════

class TestCliCollectionsCoverage:

    def test_collection_delete(self, runner, temp_db):
        from equinox.cli.main import cli
        runner.invoke(cli, ["collection", "create", "Doomed"])
        result = runner.invoke(cli, ["collection", "delete", "1"])
        assert result.exit_code == 0
        assert "deleted" in result.output.lower()

    def test_collection_create_with_desc(self, runner, temp_db):
        from equinox.cli.main import cli
        result = runner.invoke(cli, [
            "collection", "create", "My API", "-d", "A description",
        ])
        assert result.exit_code == 0

    def test_collection_requests_empty(self, runner, temp_db):
        from equinox.cli.main import cli
        runner.invoke(cli, ["collection", "create", "Empty"])
        result = runner.invoke(cli, ["collection", "requests", "1"])
        assert result.exit_code == 0
        assert "No requests" in result.output

    def test_collection_requests_with_data(self, runner, temp_db):
        from equinox.cli.main import cli
        runner.invoke(cli, ["collection", "create", "WithReqs"])
        db = Database(temp_db)
        mgr = CollectionManager(db)
        col_id = mgr.list_collections()[0]["id"]
        mgr.save_request(Request(method="GET", url="https://example.com", name="R1"),
                         collection_id=col_id, name="R1")
        result = runner.invoke(cli, ["collection", "requests", str(col_id)])
        assert result.exit_code == 0
        assert "R1" in result.output

    @patch("equinox.core.client.HTTPClient")
    def test_collection_run_success(self, MockHTTPClient, runner, temp_db):
        from equinox.cli.main import cli
        mock_client = MockHTTPClient.return_value
        mock_client.send.return_value = _mock_response()
        db = Database(temp_db)
        mgr = CollectionManager(db)
        col_id = mgr.create_collection("RunMe")
        mgr.save_request(Request(method="GET", url="https://example.com", name="R"),
                         collection_id=col_id, name="R")
        result = runner.invoke(cli, ["collection", "run", str(col_id)])
        assert result.exit_code == 0, result.output
        assert "passed" in result.output.lower()

    @patch("equinox.core.client.HTTPClient")
    def test_collection_run_with_failure(self, MockHTTPClient, runner, temp_db):
        from equinox.cli.main import cli
        mock_client = MockHTTPClient.return_value
        mock_client.send.return_value = _mock_response(status=500)
        db = Database(temp_db)
        mgr = CollectionManager(db)
        col_id = mgr.create_collection("FailMe")
        mgr.save_request(Request(method="GET", url="https://example.com", name="R"),
                         collection_id=col_id, name="R")
        result = runner.invoke(cli, ["collection", "run", str(col_id)])
        assert result.exit_code != 0

    def test_collection_run_not_found(self, runner, temp_db):
        from equinox.cli.main import cli
        result = runner.invoke(cli, ["collection", "run", "999"])
        assert result.exit_code != 0

    @patch("equinox.core.client.HTTPClient")
    def test_collection_run_stop_on_error(self, MockHTTPClient, runner, temp_db):
        from equinox.cli.main import cli
        mock_client = MockHTTPClient.return_value
        mock_client.send.return_value = _mock_response(status=500)
        db = Database(temp_db)
        mgr = CollectionManager(db)
        col_id = mgr.create_collection("Stop")
        mgr.save_request(Request(method="GET", url="https://a.com", name="R1"),
                         collection_id=col_id, name="R1")
        mgr.save_request(Request(method="GET", url="https://b.com", name="R2"),
                         collection_id=col_id, name="R2")
        result = runner.invoke(cli, [
            "collection", "run", str(col_id), "--stop-on-error",
        ])
        assert "Stopped" in result.output

    def test_collection_export_postman(self, runner, temp_db, tmp_path):
        from equinox.cli.main import cli
        db = Database(temp_db)
        mgr = CollectionManager(db)
        col_id = mgr.create_collection("Export Me")
        mgr.save_request(Request(method="GET", url="https://example.com", name="R"),
                         collection_id=col_id, name="R")
        out = str(tmp_path / "export.json")
        result = runner.invoke(cli, [
            "collection", "export", str(col_id), "-f", "postman", "-o", out,
        ])
        assert result.exit_code == 0
        assert Path(out).exists()

    def test_collection_variable_commands(self, runner, temp_db):
        from equinox.cli.main import cli
        runner.invoke(cli, ["collection", "create", "VarCol"])
        result = runner.invoke(cli, [
            "collection", "add-var", "1", "BASE_URL", "https://api.example.com",
        ])
        assert result.exit_code == 0

        result = runner.invoke(cli, ["collection", "show-vars", "1"])
        assert result.exit_code == 0
        assert "BASE_URL" in result.output

        result = runner.invoke(cli, [
            "collection", "remove-var", "1", "BASE_URL",
        ])
        assert result.exit_code == 0

    def test_collection_show_vars_not_found(self, runner, temp_db):
        from equinox.cli.main import cli
        result = runner.invoke(cli, ["collection", "show-vars", "999"])
        assert result.exit_code != 0


# ═════════════════════════════════════════════════════════════════════════════
# cli/history.py
# ═════════════════════════════════════════════════════════════════════════════

class TestCliHistoryCoverage:

    def _seed_history(self, db_path):
        db = Database(db_path)
        from equinox.storage import HistoryManager
        hm = HistoryManager(db)
        req = Request(method="GET", url="https://example.com")
        resp = _mock_response()
        hm.save_history(req, resp)

    def test_history_export_json(self, runner, temp_db, tmp_path):
        from equinox.cli.main import cli
        self._seed_history(temp_db)
        out = str(tmp_path / "h.json")
        result = runner.invoke(cli, ["history", "export", "-f", "json", "-o", out])
        assert result.exit_code == 0
        assert Path(out).exists()

    def test_history_export_csv(self, runner, temp_db, tmp_path):
        from equinox.cli.main import cli
        self._seed_history(temp_db)
        out = str(tmp_path / "h.csv")
        result = runner.invoke(cli, ["history", "export", "-f", "csv", "-o", out])
        assert result.exit_code == 0
        assert Path(out).exists()

    def test_history_export_har(self, runner, temp_db, tmp_path):
        from equinox.cli.main import cli
        self._seed_history(temp_db)
        out = str(tmp_path / "h.har")
        result = runner.invoke(cli, ["history", "export", "-f", "har", "-o", out])
        assert result.exit_code == 0
        assert Path(out).exists()
        har = json.loads(Path(out).read_text())
        assert "log" in har
        assert len(har["log"]["entries"]) >= 1

    def test_history_list_with_entries(self, runner, temp_db):
        from equinox.cli.main import cli
        self._seed_history(temp_db)
        result = runner.invoke(cli, ["history", "list", "-n", "5"])
        assert result.exit_code == 0
        assert "example.com" in result.output

    def test_parse_status_exact(self):
        from equinox.cli.history import _parse_status
        code, cls = _parse_status("200")
        assert code == 200
        assert cls == ""

    def test_parse_status_class(self):
        from equinox.cli.history import _parse_status
        code, cls = _parse_status("4xx")
        assert code is None
        assert cls == "4xx"

    def test_parse_status_errors(self):
        from equinox.cli.history import _parse_status
        code, cls = _parse_status("errors")
        assert code is None
        assert cls == "errors"

    def test_parse_status_invalid(self):
        from equinox.cli.history import _parse_status
        import click
        with pytest.raises(click.BadParameter):
            _parse_status("abc")

    def test_parse_status_empty(self):
        from equinox.cli.history import _parse_status
        code, cls = _parse_status("")
        assert code is None
        assert cls == ""

    def test_history_search_empty(self, runner, temp_db):
        from equinox.cli.main import cli
        result = runner.invoke(cli, ["history", "search", "-q", "nonexistent"])
        assert result.exit_code == 0
        assert "No matching" in result.output

    def test_history_search_with_results(self, runner, temp_db):
        from equinox.cli.main import cli
        self._seed_history(temp_db)
        result = runner.invoke(cli, ["history", "search", "-q", "example"])
        assert result.exit_code == 0


# ═════════════════════════════════════════════════════════════════════════════
# importers/openapi.py
# ═════════════════════════════════════════════════════════════════════════════

class TestOpenAPICoverage:

    @pytest.fixture
    def importer(self, db):
        return __import__(
            "equinox.importers.openapi", fromlist=["OpenAPIImporter"]
        ).OpenAPIImporter(CollectionManager(db))

    def test_validate_file_not_found(self, importer, tmp_path):
        with pytest.raises(ValidationError, match="not found"):
            importer._validate_file(tmp_path / "nope.json")

    def test_validate_file_wrong_extension(self, importer, tmp_path):
        f = tmp_path / "spec.txt"
        f.write_text("{}")
        with pytest.raises(ValidationError, match="JSON or YAML"):
            importer._validate_file(f)

    def test_validate_file_too_large(self, importer, tmp_path):
        f = tmp_path / "huge.json"
        f.write_bytes(b"x" * (importer.MAX_SPEC_SIZE + 1))
        with pytest.raises(ValidationError, match="too large"):
            importer._validate_file(f)

    def test_validate_spec_not_dict(self, importer):
        with pytest.raises(ValidationError, match="must be a dictionary"):
            importer._validate_spec([])

    def test_validate_spec_unsupported_version(self, importer):
        with pytest.raises(ValidationError, match="Unsupported"):
            importer._validate_spec({"openapi": "9.9.9"})

    def test_validate_spec_bad_version_format(self, importer):
        with pytest.raises(ValidationError, match="Invalid version"):
            importer._validate_spec({"openapi": "abc"})

    def test_validate_spec_no_version(self, importer):
        with pytest.raises(ValidationError, match="Missing"):
            importer._validate_spec({})

    def test_validate_spec_too_many_paths(self, importer):
        paths = {f"/path{i}": {"get": {}} for i in range(501)}
        with pytest.raises(ValidationError, match="Too many paths"):
            importer._validate_spec({"openapi": "3.0.0", "paths": paths})

    def test_get_version_swagger(self, importer):
        assert importer._get_version({"swagger": "2.0"}) == "2.0"

    def test_get_version_openapi(self, importer):
        assert importer._get_version({"openapi": "3.1"}) == "3.1"

    def test_import_file(self, importer, tmp_path):
        spec = {
            "openapi": "3.0.0",
            "info": {"title": "T", "version": "1"},
            "paths": {"/ping": {"get": {"summary": "Ping"}}},
        }
        f = tmp_path / "spec.json"
        f.write_text(json.dumps(spec))
        cid = importer.import_file(f)
        assert cid > 0

    def test_import_file_yaml(self, importer, tmp_path):
        import yaml
        spec = {
            "openapi": "3.0.0",
            "info": {"title": "YAML API", "version": "1"},
            "paths": {"/y": {"get": {"summary": "Y"}}},
        }
        f = tmp_path / "spec.yaml"
        f.write_text(yaml.dump(spec))
        cid = importer.import_file(f)
        assert cid > 0

    def test_resolve_schema_type_const_string(self):
        from equinox.importers.openapi import OpenAPIImporter
        assert OpenAPIImporter._resolve_schema_type({"const": "hello"}) == "string"

    def test_resolve_schema_type_const_int(self):
        from equinox.importers.openapi import OpenAPIImporter
        assert OpenAPIImporter._resolve_schema_type({"const": 42}) == "integer"

    def test_resolve_schema_type_const_float(self):
        from equinox.importers.openapi import OpenAPIImporter
        assert OpenAPIImporter._resolve_schema_type({"const": 3.14}) == "number"

    def test_resolve_schema_type_const_bool(self):
        from equinox.importers.openapi import OpenAPIImporter
        assert OpenAPIImporter._resolve_schema_type({"const": True}) == "boolean"

    def test_resolve_schema_type_const_list(self):
        from equinox.importers.openapi import OpenAPIImporter
        assert OpenAPIImporter._resolve_schema_type({"const": [1, 2]}) == "array"

    def test_resolve_schema_type_const_dict(self):
        from equinox.importers.openapi import OpenAPIImporter
        assert OpenAPIImporter._resolve_schema_type({"const": {"a": 1}}) == "object"

    def test_resolve_schema_type_one_of(self):
        from equinox.importers.openapi import OpenAPIImporter
        assert OpenAPIImporter._resolve_schema_type({
            "oneOf": [{"type": "string"}, {"type": "integer"}]
        }) == "string"

    def test_resolve_schema_type_list_type(self):
        from equinox.importers.openapi import OpenAPIImporter
        assert OpenAPIImporter._resolve_schema_type({
            "type": ["string", "null"]
        }) == "string"

    def test_resolve_schema_type_list_type_all_null(self):
        from equinox.importers.openapi import OpenAPIImporter
        assert OpenAPIImporter._resolve_schema_type({
            "type": ["null"]
        }) == "string"

    def test_get_parameter_example_from_example(self, importer):
        assert importer._get_parameter_example({"example": 42}, "3.0") == "42"

    def test_get_parameter_example_from_default(self, importer):
        assert importer._get_parameter_example({"default": "val"}, "3.0") == "val"

    def test_get_parameter_example_from_schema_example(self, importer):
        assert importer._get_parameter_example(
            {"schema": {"example": "schemaEx"}}, "3.0"
        ) == "schemaEx"

    def test_get_parameter_example_type_integer(self, importer):
        assert importer._get_parameter_example({"type": "integer"}, "2.0") == "0"

    def test_get_parameter_example_type_number(self, importer):
        assert importer._get_parameter_example({"type": "number"}, "2.0") == "0.0"

    def test_get_parameter_example_type_boolean(self, importer):
        assert importer._get_parameter_example({"type": "boolean"}, "2.0") == "true"

    def test_get_parameter_example_type_array(self, importer):
        assert importer._get_parameter_example({"type": "array"}, "2.0") == "[]"

    def test_get_parameter_example_type_object(self, importer):
        assert importer._get_parameter_example({"type": "object"}, "2.0") == "{}"

    def test_get_parameter_example_unknown_type(self, importer):
        assert importer._get_parameter_example({"type": "custom"}, "2.0") == "value"

    def test_get_parameter_example_list_type(self, importer):
        assert importer._get_parameter_example(
            {"schema": {"type": ["integer", "null"]}}, "3.1"
        ) == "0"

    def test_generate_example_from_schema_const(self, importer):
        result = importer._generate_example_from_schema({"const": "fixed"})
        assert json.loads(result) == "fixed"

    def test_generate_example_from_schema_object(self, importer):
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string", "example": "Alice"},
                "age": {"type": "integer"},
                "fixed": {"const": 99},
                "dflt": {"type": "string", "default": "hi"},
            },
        }
        result = json.loads(importer._generate_example_from_schema(schema))
        assert result["name"] == "Alice"
        assert result["age"] == 0
        assert result["fixed"] == 99
        assert result["dflt"] == "hi"

    def test_generate_example_from_schema_array(self, importer):
        assert importer._generate_example_from_schema({"type": "array"}) == "[]"

    def test_parse_request_body_json_example(self, importer):
        rb = {"content": {"application/json": {"example": {"key": "val"}}}}
        body = importer._parse_request_body(rb)
        assert json.loads(body) == {"key": "val"}

    def test_parse_request_body_schema_example(self, importer):
        rb = {"content": {"application/json": {"schema": {"example": {"x": 1}}}}}
        body = importer._parse_request_body(rb)
        assert json.loads(body) == {"x": 1}

    def test_parse_request_body_empty(self, importer):
        assert importer._parse_request_body({"content": {}}) is None

    def test_import_with_webhooks(self, importer, db):
        spec = {
            "openapi": "3.1",
            "info": {"title": "WH API", "version": "1"},
            "paths": {"/a": {"get": {"summary": "A"}}},
            "webhooks": {
                "newUser": {"post": {"summary": "New user webhook"}},
            },
        }
        cid = importer.import_dict(spec)
        mgr = CollectionManager(db)
        reqs = mgr.list_requests(cid)
        assert len(reqs) == 2  # 1 path + 1 webhook

    def test_import_with_path_params(self, importer, db):
        spec = {
            "openapi": "3.0.0",
            "info": {"title": "Path Params", "version": "1"},
            "paths": {
                "/users/{id}": {
                    "get": {
                        "summary": "Get user",
                        "parameters": [
                            {"name": "id", "in": "path", "schema": {"type": "integer"}},
                            {"name": "q", "in": "query", "schema": {"type": "string"}},
                            {"name": "X-Trace", "in": "header", "schema": {"type": "string"}},
                        ],
                    }
                },
            },
        }
        cid = importer.import_dict(spec)
        mgr = CollectionManager(db)
        req = mgr.get_request(mgr.list_requests(cid)[0]["id"])
        assert "{{id}}" in req.url
        assert "q" in req.params
        assert "X-Trace" in req.headers

    def test_import_relative_base_url(self, importer, db):
        spec = {
            "openapi": "3.0.0",
            "info": {"title": "Rel", "version": "1"},
            "paths": {"/health": {"get": {"summary": "Health"}}},
            # No servers block — defaults to "/"
        }
        cid = importer.import_dict(spec)
        mgr = CollectionManager(db)
        reqs = mgr.list_requests(cid)
        if reqs:
            assert "BASE_URL" in reqs[0]["url"]


# ═════════════════════════════════════════════════════════════════════════════
# storage/collections.py
# ═════════════════════════════════════════════════════════════════════════════

class TestCollectionManagerCoverage:

    def test_create_collection_empty_name(self, mgr):
        with pytest.raises(ValidationError):
            mgr.create_collection("")

    def test_create_collection_whitespace_name(self, mgr):
        with pytest.raises(ValidationError):
            mgr.create_collection("   ")

    def test_create_collection_name_too_long(self, mgr):
        with pytest.raises(ValidationError, match="too long"):
            mgr.create_collection("x" * 201)

    def test_create_collection_desc_too_long(self, mgr):
        with pytest.raises(ValidationError, match="too long"):
            mgr.create_collection("ok", "d" * 1001)

    def test_rename_collection(self, mgr, col_id):
        mgr.rename_collection(col_id, "New Name")
        col = mgr.get_collection(col_id)
        assert col["name"] == "New Name"

    def test_rename_collection_empty(self, mgr, col_id):
        with pytest.raises(ValidationError):
            mgr.rename_collection(col_id, "")

    def test_rename_collection_not_found(self, mgr):
        with pytest.raises(StorageError, match="not found"):
            mgr.rename_collection(999, "New")

    def test_rename_request(self, mgr, col_id):
        rid = mgr.save_request(
            Request(method="GET", url="https://x.com", name="Old"),
            collection_id=col_id, name="Old",
        )
        mgr.rename_request(rid, "New")
        req = mgr.get_request(rid)
        assert req.name == "New"

    def test_rename_request_not_found(self, mgr):
        with pytest.raises(StorageError, match="not found"):
            mgr.rename_request(999, "New")

    def test_rename_request_empty(self, mgr, col_id):
        rid = mgr.save_request(
            Request(method="GET", url="https://x.com", name="R"),
            collection_id=col_id, name="R",
        )
        with pytest.raises(ValidationError):
            mgr.rename_request(rid, "")

    def test_duplicate_request(self, mgr, col_id):
        rid = mgr.save_request(
            Request(method="POST", url="https://x.com", name="Original",
                    headers={"A": "1"}, body='{"x":1}'),
            collection_id=col_id, name="Original",
        )
        new_id = mgr.duplicate_request(rid)
        assert new_id != rid
        dup = mgr.get_request(new_id)
        assert "Copy of Original" in dup.name
        assert dup.method == "POST"
        assert dup.url == "https://x.com"

    def test_duplicate_request_not_found(self, mgr):
        with pytest.raises(StorageError, match="not found"):
            mgr.duplicate_request(999)

    def test_duplicate_request_custom_name(self, mgr, col_id):
        rid = mgr.save_request(
            Request(method="GET", url="https://x.com", name="R"),
            collection_id=col_id, name="R",
        )
        new_id = mgr.duplicate_request(rid, "Custom Copy")
        dup = mgr.get_request(new_id)
        assert dup.name == "Custom Copy"

    def test_update_collection(self, mgr, col_id):
        mgr.update_collection(col_id, "Updated", "New desc")
        col = mgr.get_collection(col_id)
        assert col["name"] == "Updated"
        assert col["description"] == "New desc"

    def test_delete_collection(self, mgr, col_id):
        mgr.delete_collection(col_id)
        assert mgr.get_collection(col_id) is None

    def test_list_requests_empty(self, mgr, col_id):
        assert mgr.list_requests(col_id) == []

    def test_save_and_list_requests(self, mgr, col_id):
        mgr.save_request(
            Request(method="GET", url="https://a.com", name="A"),
            collection_id=col_id, name="A",
        )
        mgr.save_request(
            Request(method="POST", url="https://b.com", name="B"),
            collection_id=col_id, name="B",
        )
        reqs = mgr.list_requests(col_id)
        assert len(reqs) == 2

    def test_delete_request(self, mgr, col_id):
        rid = mgr.save_request(
            Request(method="GET", url="https://x.com", name="R"),
            collection_id=col_id, name="R",
        )
        mgr.delete_request(rid)
        assert mgr.get_request(rid) is None

    def test_save_request_with_params_list(self, mgr, col_id):
        req = Request(method="GET", url="https://x.com", name="P",
                      params={"a": "1"})
        req.params_list = [
            {"key": "a", "value": "1", "enabled": True},
            {"key": "b", "value": "2", "enabled": False},
        ]
        rid = mgr.save_request(req, collection_id=col_id, name="P")
        loaded = mgr.get_request(rid)
        assert "a" in loaded.params
        # disabled param "b" should not be in params dict
        assert "b" not in loaded.params

    def test_folder_operations(self, mgr, col_id):
        mgr.create_folder(col_id, "Auth")
        mgr.create_folder(col_id, "Auth/OAuth")
        folders = mgr.list_folders(col_id)
        assert "Auth" in folders
        assert "Auth/OAuth" in folders

        mgr.delete_folder(col_id, "Auth/OAuth")
        folders = mgr.list_folders(col_id)
        assert "Auth/OAuth" not in folders

    def test_variable_operations(self, mgr, col_id):
        mgr.add_variable(col_id, "API_KEY", "secret", "My API key")
        vars_list = mgr.list_collection_variables(col_id)
        assert any(v["key"] == "API_KEY" for v in vars_list)

        all_vars = mgr.get_all_collection_variables(col_id)
        assert all_vars.get("API_KEY") == "secret"

        mgr.remove_variable(col_id, "API_KEY")
        vars_list = mgr.list_collection_variables(col_id)
        assert not any(v["key"] == "API_KEY" for v in vars_list)

    def test_move_request(self, mgr, col_id):
        rid = mgr.save_request(
            Request(method="GET", url="https://x.com", name="R"),
            collection_id=col_id, name="R",
        )
        col2 = mgr.create_collection("Other")
        mgr.move_request(rid, col2)
        # Request should now be in the other collection
        reqs = mgr.list_requests(col2)
        assert any(r["id"] == rid for r in reqs)

    def test_update_request_auth(self, mgr, col_id):
        from equinox.auth import BearerAuth
        rid = mgr.save_request(
            Request(method="GET", url="https://x.com", name="R"),
            collection_id=col_id, name="R",
        )
        mgr.update_request_auth(rid, BearerAuth(token="new-tok"))
        loaded = mgr.get_request(rid)
        assert loaded.auth is not None
        assert loaded.auth.token == "new-tok"

    def test_update_request_auth_clear(self, mgr, col_id):
        from equinox.auth import BearerAuth
        rid = mgr.save_request(
            Request(method="GET", url="https://x.com", name="R",
                    auth=BearerAuth(token="old")),
            collection_id=col_id, name="R",
        )
        mgr.update_request_auth(rid, None)
        loaded = mgr.get_request(rid)
        assert loaded.auth is None

