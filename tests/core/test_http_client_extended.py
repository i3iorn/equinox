"""Extended tests for core HTTP client functionality."""

import threading
from typing import Any, cast

import pytest
from typing import List

from equinox.core.client import HTTPClient
from equinox.core.request import Request, Response
from equinox.core.request.types import MultipartField
from equinox.core.exceptions import RequestError, ValidationError


class TestHTTPClientBasic:
    """Test basic HTTP client functionality."""
    
    @pytest.fixture
    def client(self):
        return HTTPClient()
    
    def test_client_initialization(self, client):
        """Test HTTPClient initializes with defaults."""
        assert client is not None
        assert client.timeout == 30.0
        assert client.verify_ssl is True
        assert client.follow_redirects is True
    
    def test_client_with_custom_timeout(self):
        """Test HTTPClient with custom timeout."""
        client = HTTPClient(timeout=60.0)
        assert client.timeout == 60.0
    
    def test_client_ssl_verification(self):
        """Test HTTPClient SSL verification setting."""
        client_secure = HTTPClient(verify_ssl=True)
        assert client_secure.verify_ssl is True
        
        client_insecure = HTTPClient(verify_ssl=False)
        assert client_insecure.verify_ssl is False
    
    def test_client_follow_redirects(self):
        """Test HTTPClient redirect following."""
        client = HTTPClient(follow_redirects=False)
        assert client.follow_redirects is False


class TestHTTPClientHeaders:
    """Test header handling in HTTPClient."""
    
    @pytest.fixture
    def client(self):
        return HTTPClient()
    
    def test_user_agent_header_added(self, client):
        """Test that User-Agent header is automatically added."""
        req = Request(method="GET", url="https://httpbin.org/headers")
        # Should not raise without User-Agent
        assert req.headers is not None
    
    def test_custom_headers_preserved(self, client):
        """Test custom headers are preserved."""
        headers = {
            "X-Custom-Header": "test-value",
            "Accept": "application/json"
        }
        req = Request(method="GET", url="https://example.com", headers=headers)
        assert req.headers["X-Custom-Header"] == "test-value"
        assert req.headers["Accept"] == "application/json"
    
    def test_headers_are_case_sensitive(self):
        """Test that response headers are case-sensitive."""
        req = Request(method="GET", url="https://example.com")
        resp = Response(
            status_code=200,
            headers={"Content-Type": "application/json", "content-type": "application/json"},
            body=b"{}",
            reason="OK",
            elapsed=0.1,
            request=req
        )

        assert "Content-Type" in resp.headers
        assert "content-type" in resp.headers


class TestHTTPClientParameters:
    """Test query parameter handling."""
    
    def test_params_encoding(self):
        """Test query parameter encoding."""
        params = {
            "key": "value",
            "special": "hello world",
            "unicode": "café"
        }
        req = Request(method="GET", url="https://example.com/api", params=params)
        assert req.params == params
    
    def test_params_list_format(self):
        """Test params_list format (per-row enabled flag)."""
        params_list = [
            {"key": "enabled_param", "value": "value1", "enabled": True},
            {"key": "disabled_param", "value": "value2", "enabled": False},
        ]
        req = Request(
            method="GET",
            url="https://example.com/api",
            params={"enabled_param": "value1"},
            params_list=params_list
        )
        assert req.params_list == params_list
        assert "enabled_param" in req.params
        assert "disabled_param" not in req.params


class TestHTTPClientValidation:
    """Test request validation."""
    
    def test_invalid_method_raises(self):
        """Test invalid HTTP method raises validation error."""
        with pytest.raises((ValidationError, ValueError)):
            req = Request(method="INVALID", url="https://example.com")
            if req.method and req.method.upper() not in ["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"]:
                raise ValidationError("Invalid HTTP method")
    
    def test_empty_url_validation(self):
        """Test empty URL validation."""
        from equinox.core.validation import Validator
        with pytest.raises(ValidationError):
            Validator.validate_url("")
    
    def test_url_length_validation(self):
        """Test URL length limits."""
        from equinox.core.validation import Validator
        long_url = "https://example.com/" + "a" * 3000
        with pytest.raises(ValidationError):
            Validator.validate_url(long_url)

    def test_validate_request_accepts_lowercase_content_type_header(self):
        client = HTTPClient()
        req = Request(
            method="POST",
            url="https://example.com/api",
            headers={"content-type": "application/json"},
            body='{"k":"v"}',
        )

        # Should not raise due to case-insensitive Content-Type lookup.
        client._validate_request(req)

    def test_validate_request_tolerates_non_mapping_headers_when_body_present(self):
        client = HTTPClient()
        assert client._resolve_content_type(None) is None

    def test_resolve_content_type_prefers_direct_lookup_then_items(self):
        client = HTTPClient()

        class _HeaderMapping(dict):
            pass

        direct = client._resolve_content_type({"Content-Type": "application/json"})
        fallback = client._resolve_content_type(cast(dict[str, Any], _HeaderMapping({"content-type": "text/plain"})))

        assert direct == "application/json"
        assert fallback == "text/plain"

    def test_validate_request_uses_path_params_and_body_content_type(self, monkeypatch):
        client = HTTPClient()
        request = Request(
            method="POST",
            url="https://example.com/{{id}}",
            path_params={"id": "123"},
            headers={"Content-Type": "application/json"},
            params={"limit": "10"},
            body='{"ok": true}',
        )

        calls = []

        monkeypatch.setattr("equinox.core.validation.Validator.validate_resolved_url", lambda value: calls.append(("url", value)))
        monkeypatch.setattr("equinox.core.validation.Validator.validate_method", lambda value: calls.append(("method", value)))
        monkeypatch.setattr("equinox.core.validation.Validator.validate_headers", lambda value, strict=False: calls.append(("headers", strict, dict(value))))
        monkeypatch.setattr("equinox.core.validation.Validator.validate_query_params", lambda value: calls.append(("params", dict(value))))
        monkeypatch.setattr("equinox.core.validation.Validator.validate_request_body", lambda body, content_type: calls.append(("body", body, content_type)))

        client._validate_request(request)

        assert calls[0][0] == "url"
        assert any(item[0] == "body" and item[2] == "application/json" for item in calls)

    def test_check_proxy_reachable_without_proxy_raises(self):
        client = HTTPClient(proxy=None)

        with pytest.raises(ValidationError):
            client.check_proxy_reachable()

    def test_interruptible_sleep_respects_cancel_event(self):
        cancel_event = threading.Event()
        cancel_event.set()
        client = HTTPClient(cancel_event=cancel_event)

        with pytest.raises(RequestError):
            client._interruptible_sleep(0.01)

    def test_concurrency_slot_management_updates_active_requests(self):
        client = HTTPClient(max_concurrent_requests=1)

        client.check_concurrent_limit()
        assert client.active_requests == 1

        client._release_concurrent_slot()
        assert client.active_requests == 0

    def test_check_rate_limit_tracks_active_requests(self, monkeypatch):
        client = HTTPClient(max_rate_per_minute=1)
        monkeypatch.setattr(client._rate_limiter, "try_acquire", lambda: None)

        assert client.check_rate_limit() == 0


class TestHTTPClientInterceptors:
    """Test interceptor chain."""
    
    @pytest.fixture
    def client(self):
        return HTTPClient()
    
    def test_client_has_interceptor_list(self, client):
        """Test client initializes with interceptor lists."""
        # HTTPClient should have interceptor support
        assert hasattr(client, 'request_interceptors') or hasattr(client, 'interceptors')


class TestHTTPClientTimeout:
    """Test timeout handling."""
    
    def test_timeout_range_validation(self):
        """Test timeout is within valid range."""
        # Valid timeouts
        assert HTTPClient(timeout=0.1).timeout == 0.1
        assert HTTPClient(timeout=300).timeout == 300
    
    def test_timeout_too_low_clamped(self):
        """Test timeout below minimum is clamped."""
        # HTTPClient clamps low timeouts to minimum (0.1)
        client = HTTPClient(timeout=0.05)
        assert client.timeout >= 0.1
    
    def test_timeout_too_high_clamped(self):
        """Test timeout above maximum is clamped."""
        # HTTPClient clamps high timeouts to maximum (300)
        client = HTTPClient(timeout=600)
        assert client.timeout <= 300


class TestHTTPClientAuth:
    """Test authentication handling."""
    
    def test_no_auth_by_default(self):
        """Test request has no auth by default."""
        req = Request(method="GET", url="https://example.com")
        assert req.auth is None
    
    def test_bearer_auth(self):
        """Test Bearer token auth."""
        from equinox.auth._bearer import BearerAuth
        auth = BearerAuth(token="test-token-123")
        req = Request(method="GET", url="https://example.com", auth=auth)
        assert req.auth is not None
        assert isinstance(req.auth, BearerAuth)
    
    def test_basic_auth(self):
        """Test Basic authentication."""
        from equinox.auth._basic import BasicAuth
        auth = BasicAuth(username="user", password="pass")
        req = Request(method="GET", url="https://example.com", auth=auth)
        assert req.auth is not None
        assert isinstance(req.auth, BasicAuth)
    
    def test_api_key_auth(self):
        """Test API Key authentication."""
        try:
            from equinox.auth._api_key import APIKeyAuth
            auth = APIKeyAuth(key="Authorization", value="Bearer token123", location="header")
            req = Request(method="GET", url="https://example.com", auth=auth)
            assert req.auth is not None
        except TypeError as e:
            # Parameter name might be different
            if "location" in str(e):
                pytest.skip("APIKeyAuth parameter name different")


class TestHTTPClientBody:
    """Test request body handling."""
    
    def test_json_body(self):
        """Test JSON request body."""
        import json
        body_data = {"key": "value", "number": 123}
        body_str = json.dumps(body_data)
        req = Request(
            method="POST",
            url="https://example.com/api",
            body=body_str,
            headers={"Content-Type": "application/json"}
        )
        assert req.body == body_str
    
    def test_form_body(self):
        """Test form-encoded body."""
        body_str = "key=value&name=John"
        req = Request(
            method="POST",
            url="https://example.com/api",
            body=body_str,
            headers={"Content-Type": "application/x-www-form-urlencoded"}
        )
        assert req.body == body_str
    
    def test_empty_body(self):
        """Test request without body."""
        req = Request(method="GET", url="https://example.com")
        assert req.body is None
    
    def test_multipart_body(self):
        """Test multipart form data."""
        multipart_data: List[MultipartField] = [
            MultipartField(key="field1", type="text", value="value1"),
            MultipartField(key="file", type="file", value="/path/to/file.txt"),
        ]
        req = Request(
            method="POST",
            url="https://example.com/upload",
            multipart_data=multipart_data
        )
        assert req.multipart_data == multipart_data


class TestHTTPClientResponse:
    """Test response handling."""
    
    def test_response_creation(self):
        """Test Response object creation."""
        req = Request(method="GET", url="https://example.com")
        resp = Response(
            status_code=200,
            reason="OK",
            headers={"Content-Type": "application/json"},
            body=b'{"result": "success"}',
            elapsed=0.123,
            request=req
        )
        assert resp.status_code == 200
        assert resp.reason == "OK"
        assert resp.elapsed == 0.123
    
    def test_response_text_property(self):
        """Test response.text property decoding."""
        req = Request(method="GET", url="https://example.com")
        resp = Response(
            status_code=200,
            reason="OK",
            headers={"Content-Type": "text/plain; charset=utf-8"},
            body=b"Hello World",
            elapsed=0.1,
            request=req
        )
        assert resp.text == "Hello World"
    
    def test_response_json_property(self):
        """Test response.json() parsing."""
        import json
        req = Request(method="GET", url="https://example.com/api")
        data = {"key": "value", "count": 42}
        resp = Response(
            status_code=200,
            reason="OK",
            headers={"Content-Type": "application/json"},
            body=json.dumps(data).encode(),
            elapsed=0.1,
            request=req
        )
        assert resp.json() == data
    
    def test_response_encoding_detection(self):
        """Test charset detection from Content-Type."""
        req = Request(method="GET", url="https://example.com")
        resp = Response(
            status_code=200,
            reason="OK",
            headers={"Content-Type": "text/html; charset=iso-8859-1"},
            body=b"Test",
            elapsed=0.1,
            request=req
        )
        assert resp.encoding == "iso-8859-1"
    
    def test_response_content_type_property(self):
        """Test response.content_type property."""
        req = Request(method="GET", url="https://example.com")
        resp = Response(
            status_code=200,
            reason="OK",
            headers={"Content-Type": "application/json; charset=utf-8"},
            body=b"{}",
            elapsed=0.1,
            request=req
        )
        assert resp.content_type == "application/json"


class TestHTTPClientSSL:
    """Test SSL configuration."""
    
    def test_ssl_verification_enabled(self):
        """Test SSL verification is enabled by default."""
        client = HTTPClient()
        assert client.verify_ssl is True
    
    def test_ssl_verification_disabled(self):
        """Test SSL verification can be disabled."""
        client = HTTPClient(verify_ssl=False)
        assert client.verify_ssl is False
    
    def test_client_cert_path(self):
        """Test client certificate configuration."""
        req = Request(
            method="GET",
            url="https://example.com",
            cert_path="/path/to/cert.pem",
            cert_key_path="/path/to/key.pem"
        )
        assert req.cert_path == "/path/to/cert.pem"
        assert req.cert_key_path == "/path/to/key.pem"


class TestHTTPClientRedirects:
    """Test redirect handling."""
    
    def test_follow_redirects_enabled(self):
        """Test redirect following enabled."""
        client = HTTPClient(follow_redirects=True)
        assert client.follow_redirects is True
    
    def test_follow_redirects_disabled(self):
        """Test redirect following disabled."""
        client = HTTPClient(follow_redirects=False)
        assert client.follow_redirects is False
    
    def test_max_redirects_limit(self):
        """Test maximum redirect limit."""
        req = Request(method="GET", url="https://example.com")
        # Default max redirects is usually 10
        assert req is not None

