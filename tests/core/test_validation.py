"""Tests for input validation module."""

import pytest

from equinox.core.exceptions import ValidationError
from equinox.core.validation import Validator


class TestURLValidation:
    """Tests for URL validation."""

    def test_valid_http_url(self):
        """Test valid HTTP URL passes basic checks."""
        url = "http://example.com/api/users"
        assert Validator.validate_url(url) == url

    def test_valid_https_url(self):
        """Test valid HTTPS URL passes basic checks."""
        url = "https://api.example.com/v1/data"
        assert Validator.validate_url(url) == url

    def test_template_url_passes_basic_checks(self):
        """Template URLs with {{vars}} pass validate_url (string-only)."""
        url = "{{baseUrl}}/api/users"
        assert Validator.validate_url(url) == url

    def test_relative_path_passes_basic_checks(self):
        """Relative paths pass validate_url (validated at send-time)."""
        url = "/api/users"
        assert Validator.validate_url(url) == url

    def test_invalid_scheme_resolved(self):
        """Fully resolved URL with invalid scheme rejected by validate_resolved_url."""
        with pytest.raises(ValidationError, match="Invalid URL scheme"):
            Validator.validate_resolved_url("ftp://example.com")

    def test_missing_hostname_resolved(self):
        """Fully resolved URL without hostname rejected by validate_resolved_url."""
        with pytest.raises(ValidationError, match="hostname"):
            Validator.validate_resolved_url("https://")

    def test_resolved_url_valid(self):
        """Valid fully resolved URL passes validate_resolved_url."""
        url = "https://api.example.com/v1/data"
        assert Validator.validate_resolved_url(url) == url

    def test_url_too_long(self):
        """Test URL exceeding maximum length."""
        long_url = "https://example.com/" + "a" * 3000
        with pytest.raises(ValidationError, match="maximum length"):
            Validator.validate_url(long_url)

    def test_empty_url(self):
        """Test empty URL."""
        with pytest.raises(ValidationError):
            Validator.validate_url("")

    def test_none_url(self):
        """Test None URL."""
        with pytest.raises(ValidationError):
            Validator.validate_url(None)


class TestHeaderValidation:
    """Tests for HTTP header validation."""

    def test_valid_header_name(self):
        """Test valid header name."""
        assert Validator.validate_header_name("Content-Type") == "Content-Type"
        assert Validator.validate_header_name("X-API-Key") == "X-API-Key"

    def test_invalid_header_name_chars(self):
        """Test header name with invalid characters."""
        with pytest.raises(ValidationError, match="Invalid header name"):
            Validator.validate_header_name("Content Type")  # Space not allowed

    def test_dangerous_header(self):
        """Test dangerous header that should be blocked."""
        with pytest.raises(ValidationError, match="Cannot manually set"):
            Validator.validate_header_name("Host")

    def test_header_value_with_crlf(self):
        """Test header value with CRLF injection attempt."""
        with pytest.raises(ValidationError, match="CRLF"):
            Validator.validate_header_value("value\r\nInjected-Header: evil")

    def test_valid_headers_dict(self):
        """Test valid headers dictionary."""
        headers = {
            "Content-Type": "application/json",
            "X-API-Key": "test-key",
        }
        validated = Validator.validate_headers(headers)
        assert "Content-Type" in validated
        assert "X-API-Key" in validated

    def test_too_many_headers(self):
        """Test exceeding maximum header count."""
        headers = {f"Header-{i}": "value" for i in range(150)}
        with pytest.raises(ValidationError, match="Too many headers"):
            Validator.validate_headers(headers)


class TestRequestBodyValidation:
    """Tests for request body validation."""

    def test_valid_json_body(self):
        """Test valid JSON body."""
        body = '{"name": "John", "age": 30}'
        validated = Validator.validate_request_body(body, "application/json")
        assert validated == body

    def test_invalid_json_body(self):
        """Test invalid JSON body."""
        body = "{invalid json}"
        with pytest.raises(ValidationError, match="Invalid JSON"):
            Validator.validate_request_body(body, "application/json")

    def test_body_too_large(self):
        """Test body exceeding size limit."""
        large_body = "x" * (101 * 1024 * 1024)  # 101MB
        with pytest.raises(ValidationError, match="too large"):
            Validator.validate_request_body(large_body)

    def test_none_body(self):
        """Test None body (should be allowed)."""
        assert Validator.validate_request_body(None) is None


class TestQueryParamsValidation:
    """Tests for query parameter validation."""

    def test_valid_params(self):
        """Test valid query parameters."""
        params = {"key": "value", "page": "1"}
        validated = Validator.validate_query_params(params)
        assert validated == params

    def test_too_many_params(self):
        """Test exceeding maximum parameter count."""
        params = {f"param{i}": f"value{i}" for i in range(150)}
        with pytest.raises(ValidationError, match="Too many parameters"):
            Validator.validate_query_params(params)

    def test_param_with_injection(self):
        """Test parameter with CRLF injection attempt."""
        params = {"cmd": "value\r\nX-Evil: injected"}
        with pytest.raises(ValidationError, match="CRLF"):
            Validator.validate_query_params(params)


class TestFilePathValidation:
    """Tests for file path validation."""

    def test_valid_path(self):
        """Test valid file path."""
        path = Validator.validate_file_path("test.txt")
        assert path.name == "test.txt"

    def test_path_traversal_dotdot(self):
        """Test path traversal with .."""
        with pytest.raises(ValidationError, match="traversal"):
            Validator.validate_file_path("../../../etc/passwd")

    def test_path_traversal_backslash(self):
        """Test path traversal with backslash."""
        with pytest.raises(ValidationError, match="traversal"):
            Validator.validate_file_path("..\\..\\windows\\system32")

    def test_path_with_base_dir(self, tmp_path):
        """Test path validation with base directory restriction."""
        # Valid path within base dir
        test_file = tmp_path / "test.txt"
        validated = Validator.validate_file_path(str(test_file), tmp_path)
        assert validated == test_file.resolve()

        # Invalid path outside base dir
        outside_path = tmp_path.parent / "outside.txt"
        with pytest.raises(ValidationError, match="outside allowed directory"):
            Validator.validate_file_path(str(outside_path), tmp_path)


class TestEnvironmentVariableValidation:
    """Tests for environment variable validation."""

    def test_valid_variable(self):
        """Test valid environment variable."""
        name, value = Validator.validate_environment_variable("API_URL", "https://api.example.com")
        assert name == "API_URL"
        assert value == "https://api.example.com"

    def test_invalid_variable_name(self):
        """Test invalid variable name format."""
        with pytest.raises(ValidationError, match="Invalid variable name"):
            Validator.validate_environment_variable("api-url", "value")

    def test_variable_with_injection(self):
        """Test variable value with injection attempt."""
        with pytest.raises(ValidationError, match="dangerous pattern"):
            Validator.validate_environment_variable("CMD", "$(rm -rf /)")


class TestMethodValidation:
    """Tests for HTTP method validation."""

    def test_valid_methods(self):
        """Test valid HTTP methods."""
        for method in ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]:
            assert Validator.validate_method(method) == method

    def test_lowercase_method(self):
        """Test lowercase method (should be uppercased)."""
        assert Validator.validate_method("get") == "GET"

    def test_invalid_method(self):
        """Test invalid HTTP method."""
        with pytest.raises(ValidationError, match="Invalid HTTP method"):
            Validator.validate_method("INVALID")


class TestSanitization:
    """Tests for text sanitization."""

    def test_sanitize_long_text(self):
        """Test sanitizing long text."""
        long_text = "x" * 2000
        sanitized = Validator.sanitize_for_display(long_text, max_length=100)
        assert len(sanitized) <= 103  # 100 + "..."

    def test_sanitize_control_chars(self):
        """Test sanitizing control characters."""
        text_with_control = "Hello\x00\x01World"
        sanitized = Validator.sanitize_for_display(text_with_control)
        assert "\x00" not in sanitized
        assert "\x01" not in sanitized
