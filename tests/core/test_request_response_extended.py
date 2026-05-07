"""Extended tests for request and response models."""

import pytest
import json
from datetime import datetime, timezone
from equinox.core.request import Request, Response
from equinox.auth import BearerAuth, BasicAuth, APIKeyAuth


class TestRequestModel:
    """Test Request model."""
    
    def test_create_simple_get_request(self):
        """Test creating a simple GET request."""
        req = Request(method="GET", url="https://api.example.com/users")
        assert req.method == "GET"
        assert req.url == "https://api.example.com/users"
        assert req.headers == {}
        assert req.params == {}
        assert req.body is None
    
    def test_create_post_request_with_body(self):
        """Test creating POST request with body."""
        body = json.dumps({"name": "John", "age": 30})
        req = Request(
            method="POST",
            url="https://api.example.com/users",
            body=body,
            headers={"Content-Type": "application/json"}
        )
        assert req.method == "POST"
        assert req.body == body
        assert "Content-Type" in req.headers
    
    def test_request_with_query_params(self):
        """Test request with query parameters."""
        req = Request(
            method="GET",
            url="https://api.example.com/users",
            params={"page": "1", "limit": "10"}
        )
        assert req.params["page"] == "1"
        assert req.params["limit"] == "10"
    
    def test_request_with_custom_headers(self):
        """Test request with custom headers."""
        headers = {
            "Authorization": "Bearer token123",
            "X-Custom-Header": "custom-value",
            "Accept": "application/json"
        }
        req = Request(method="GET", url="https://api.example.com", headers=headers)
        assert req.headers == headers
    
    def test_request_with_auth(self):
        """Test request with authentication."""
        auth = BearerAuth(token="secret-token")
        req = Request(method="GET", url="https://api.example.com", auth=auth)
        assert req.auth == auth
    
    def test_request_with_metadata(self):
        """Test request with metadata."""
        req = Request(
            method="GET",
            url="https://api.example.com/users",
            name="Get Users",
            description="Retrieve list of all users",
            collection_id=1,
            folder="admin"
        )
        assert req.name == "Get Users"
        assert req.description == "Retrieve list of all users"
        assert req.collection_id == 1
        assert req.folder == "admin"
    
    def test_request_with_timeout(self):
        """Test request with custom timeout."""
        req = Request(method="GET", url="https://api.example.com", timeout=60.0)
        assert req.timeout == 60.0
    
    def test_request_ssl_verification(self):
        """Test SSL verification setting."""
        req_secure = Request(method="GET", url="https://api.example.com", verify_ssl=True)
        assert req_secure.verify_ssl is True
        
        req_insecure = Request(method="GET", url="https://api.example.com", verify_ssl=False)
        assert req_insecure.verify_ssl is False
    
    def test_request_follow_redirects(self):
        """Test redirect following setting."""
        req = Request(method="GET", url="https://api.example.com", follow_redirects=False)
        assert req.follow_redirects is False
    
    def test_request_with_path_params(self):
        """Test request with path parameters."""
        req = Request(
            method="GET",
            url="https://api.example.com/users/{id}",
            path_params={"id": "123"}
        )
        assert req.path_params == {"id": "123"}
    
    def test_request_with_multipart_data(self):
        """Test request with multipart form data."""
        multipart = [
            {"key": "field1", "type": "text", "value": "value1"},
            {"key": "file", "type": "file", "value": "/path/to/file.txt"}
        ]
        req = Request(
            method="POST",
            url="https://api.example.com/upload",
            multipart_data=multipart
        )
        assert req.multipart_data == multipart
    
    def test_request_with_captures(self):
        """Test request with capture rules."""
        captures = [
            {"variable": "token", "source": "body", "path": "$.access_token"},
            {"variable": "user_id", "source": "header", "path": "X-User-ID"}
        ]
        req = Request(method="GET", url="https://api.example.com", captures=captures)
        assert req.captures == captures
    
    def test_request_with_assertions(self):
        """Test request with test assertions."""
        assertions = [
            {"type": "status", "expected": 200},
            {"type": "body_contains", "expected": "success"}
        ]
        req = Request(method="GET", url="https://api.example.com", assertions=assertions)
        assert req.assertions == assertions
    
    def test_request_with_scripts(self):
        """Test request with pre/post scripts."""
        pre_script = "print('Before request')"
        post_script = "print('After request')"
        req = Request(
            method="GET",
            url="https://api.example.com",
            pre_script=pre_script,
            post_script=post_script
        )
        assert req.pre_script == pre_script
        assert req.post_script == post_script
    
    def test_request_with_client_cert(self):
        """Test request with client certificate."""
        req = Request(
            method="GET",
            url="https://api.example.com",
            cert_path="/path/to/cert.pem",
            cert_key_path="/path/to/key.pem"
        )
        assert req.cert_path == "/path/to/cert.pem"
        assert req.cert_key_path == "/path/to/key.pem"
    
    def test_request_to_dict(self):
        """Test converting request to dictionary."""
        req = Request(
            method="POST",
            url="https://api.example.com/users",
            body='{"name": "John"}',
            headers={"Content-Type": "application/json"},
            name="Create User"
        )
        req_dict = req.to_dict()
        assert req_dict["method"] == "POST"
        assert req_dict["url"] == "https://api.example.com/users"
        assert req_dict["body"] == '{"name": "John"}'
        assert req_dict["name"] == "Create User"
    
    def test_request_to_dict_with_auth(self):
        """Test converting request with auth to dict."""
        auth = BearerAuth(token="secret")
        req = Request(method="GET", url="https://api.example.com", auth=auth)
        req_dict = req.to_dict()
        assert "auth" in req_dict
        assert "auth_type" in req_dict
    
    def test_request_from_dict(self):
        """Test creating request from dictionary."""
        req_dict = {
            "method": "GET",
            "url": "https://api.example.com/users",
            "headers": {"Accept": "application/json"},
            "name": "List Users"
        }
        req = Request.from_dict(req_dict)
        assert req.method == "GET"
        assert req.url == "https://api.example.com/users"
        assert req.name == "List Users"
    
    def test_request_default_values(self):
        """Test request defaults."""
        req = Request(method="GET", url="https://api.example.com")
        assert req.timeout == 30.0
        assert req.verify_ssl is True
        assert req.follow_redirects is True
        assert req.headers == {}
        assert req.params == {}
        assert req.auth is None


class TestResponseModel:
    """Test Response model."""
    
    def test_create_successful_response(self):
        """Test creating successful response."""
        req = Request(method="GET", url="https://api.example.com")
        resp = Response(
            status_code=200,
            reason="OK",
            headers={"Content-Type": "application/json"},
            body=b'{"status": "success"}',
            elapsed=0.123,
            request=req
        )
        assert resp.status_code == 200
        assert resp.reason == "OK"
        assert resp.elapsed == 0.123
    
    def test_response_error_status(self):
        """Test response with error status code."""
        req = Request(method="GET", url="https://api.example.com")
        resp = Response(
            status_code=404,
            reason="Not Found",
            headers={},
            body=b'{"error": "Not Found"}',
            elapsed=0.05,
            request=req
        )
        assert resp.status_code == 404
    
    def test_response_text_decoding(self):
        """Test response text decoding."""
        req = Request(method="GET", url="https://api.example.com")
        resp = Response(
            status_code=200,
            reason="OK",
            headers={"Content-Type": "text/plain; charset=utf-8"},
            body=b"Hello, World!",
            elapsed=0.1,
            request=req
        )
        assert resp.text == "Hello, World!"
    
    def test_response_json_parsing(self):
        """Test response JSON parsing."""
        req = Request(method="GET", url="https://api.example.com")
        data = {"id": 1, "name": "John", "active": True}
        resp = Response(
            status_code=200,
            reason="OK",
            headers={"Content-Type": "application/json"},
            body=json.dumps(data).encode(),
            elapsed=0.1,
            request=req
        )
        assert resp.json() == data
    
    def test_response_content_type_detection(self):
        """Test content type detection."""
        req = Request(method="GET", url="https://api.example.com")
        resp = Response(
            status_code=200,
            reason="OK",
            headers={"Content-Type": "application/json; charset=utf-8"},
            body=b"{}",
            elapsed=0.1,
            request=req
        )
        assert resp.content_type == "application/json"
    
    def test_response_charset_detection(self):
        """Test charset detection."""
        req = Request(method="GET", url="https://api.example.com")
        resp = Response(
            status_code=200,
            reason="OK",
            headers={"Content-Type": "text/html; charset=iso-8859-1"},
            body=b"<html></html>",
            elapsed=0.1,
            request=req
        )
        assert resp.encoding == "iso-8859-1"
    
    def test_response_default_charset(self):
        """Test default charset when not specified."""
        req = Request(method="GET", url="https://api.example.com")
        resp = Response(
            status_code=200,
            reason="OK",
            headers={"Content-Type": "text/plain"},
            body=b"text",
            elapsed=0.1,
            request=req
        )
        # Should default to utf-8
        assert resp.text == "text"
    
    def test_response_no_content_type(self):
        """Test response without Content-Type header."""
        req = Request(method="GET", url="https://api.example.com")
        resp = Response(
            status_code=200,
            reason="OK",
            headers={},
            body=b"data",
            elapsed=0.1,
            request=req
        )
        assert resp.content_type is None

    def test_response_timestamp(self):
        """Test response timestamp."""
        req = Request(method="GET", url="https://api.example.com")
        resp = Response(
            status_code=200,
            reason="OK",
            headers={},
            body=b"",
            elapsed=0.1,
            request=req
        )
        assert resp.timestamp is not None
        assert isinstance(resp.timestamp, datetime)
    
    def test_response_with_custom_timestamp(self):
        """Test response with custom timestamp."""
        req = Request(method="GET", url="https://api.example.com")
        custom_time = datetime(2026, 3, 13, 10, 30, 0)
        resp = Response(
            status_code=200,
            reason="OK",
            headers={},
            body=b"",
            elapsed=0.1,
            request=req,
            timestamp=custom_time
        )
        assert resp.timestamp == custom_time
    
    def test_response_sent_headers(self):
        """Test sent_headers capture."""
        req = Request(method="GET", url="https://api.example.com")
        sent_headers = {"Authorization": "Bearer token"}
        resp = Response(
            status_code=200,
            reason="OK",
            headers={},
            body=b"",
            elapsed=0.1,
            request=req,
            sent_headers=sent_headers
        )
        assert resp.sent_headers == sent_headers
    
    def test_response_sent_url(self):
        """Test sent_url capture (after redirects, etc)."""
        req = Request(method="GET", url="https://api.example.com")
        resp = Response(
            status_code=200,
            reason="OK",
            headers={},
            body=b"",
            elapsed=0.1,
            request=req,
            sent_url="https://api.example.com/final"
        )
        assert resp.sent_url == "https://api.example.com/final"
    
    def test_response_timings(self):
        """Test response timing breakdown."""
        req = Request(method="GET", url="https://api.example.com")
        timings = {
            "dns": 50,
            "connect": 100,
            "tls": 75,
            "request": 25,
            "wait": 150,
            "download": 50
        }
        resp = Response(
            status_code=200,
            reason="OK",
            headers={},
            body=b"data",
            elapsed=0.45,
            request=req,
            timings=timings
        )
        assert resp.timings == timings


class TestRequestResponseIntegration:
    """Test Request/Response integration."""
    
    def test_request_response_roundtrip(self):
        """Test serializing request/response and deserializing."""
        # Create request
        req = Request(
            method="POST",
            url="https://api.example.com/users",
            body='{"name": "John"}',
            headers={"Content-Type": "application/json"},
            name="Create User"
        )
        
        # Serialize
        req_dict = req.to_dict()
        
        # Deserialize
        req2 = Request.from_dict(req_dict)
        
        # Verify
        assert req2.method == req.method
        assert req2.url == req.url
        assert req2.body == req.body
        assert req2.name == req.name
    
    def test_response_with_large_body(self):
        """Test response with large body."""
        req = Request(method="GET", url="https://api.example.com")
        large_body = b"x" * (10 * 1024 * 1024)  # 10MB
        resp = Response(
            status_code=200,
            reason="OK",
            headers={"Content-Type": "application/octet-stream"},
            body=large_body,
            elapsed=1.5,
            request=req
        )
        assert len(resp.body) == len(large_body)
    
    def test_request_with_all_features(self):
        """Test request using all available features."""
        req = Request(
            method="POST",
            url="https://api.example.com/users",
            headers={"Authorization": "Bearer token", "Content-Type": "application/json"},
            params={"notify": "true"},
            body='{"name": "John", "email": "john@example.com"}',
            auth=BearerAuth(token="token"),
            timeout=30.0,
            follow_redirects=True,
            verify_ssl=True,
            name="Create User",
            description="Create a new user account",
            collection_id=1,
            folder="users",
            path_params={"version": "v1"},
            captures=[{"variable": "id", "source": "body", "path": "$.id"}],
            pre_script="print('Creating user')",
            post_script="print('User created')"
        )
        
        # Verify all fields
        assert req.method == "POST"
        assert req.url == "https://api.example.com/users"
        assert len(req.headers) == 2
        assert len(req.params) == 1
        assert req.body is not None
        assert req.auth is not None
        assert req.timeout == 30.0
        assert req.name == "Create User"

