"""Tests for core/curl_parser.py — cURL command parsing."""

import base64

import pytest

from equinox.core.curl_parser import parse_curl


class TestParseCurlBasic:
    """Basic parsing of method, URL, and flags."""

    def test_simple_get(self):
        result = parse_curl("curl https://example.com/api")
        assert result["method"] == "GET"
        assert result["url"] == "https://example.com/api"
        assert result["headers"] == {}
        assert result["body"] is None
        assert result["verify_ssl"] is True

    def test_explicit_method(self):
        result = parse_curl("curl -X DELETE https://example.com/items/1")
        assert result["method"] == "DELETE"
        assert result["url"] == "https://example.com/items/1"

    def test_long_request_flag(self):
        result = parse_curl("curl --request PUT https://example.com/items/1")
        assert result["method"] == "PUT"

    def test_method_case_normalised(self):
        result = parse_curl("curl -X patch https://example.com/items/1")
        assert result["method"] == "PATCH"

    def test_no_url_raises(self):
        with pytest.raises(ValueError, match="No URL"):
            parse_curl("curl -X GET")

    def test_url_without_curl_prefix(self):
        result = parse_curl("https://example.com/plain")
        assert result["url"] == "https://example.com/plain"
        assert result["method"] == "GET"


class TestParseCurlHeaders:
    """Header parsing via -H / --header."""

    def test_single_header(self):
        result = parse_curl('curl -H "Accept: application/json" https://example.com')
        assert result["headers"]["Accept"] == "application/json"

    def test_multiple_headers(self):
        cmd = (
            'curl -H "Accept: text/html" '
            '-H "X-Custom: value" '
            "https://example.com"
        )
        result = parse_curl(cmd)
        assert result["headers"]["Accept"] == "text/html"
        assert result["headers"]["X-Custom"] == "value"

    def test_long_header_flag(self):
        result = parse_curl('curl --header "Content-Type: application/xml" https://example.com')
        assert result["headers"]["Content-Type"] == "application/xml"

    def test_header_without_colon_ignored(self):
        result = parse_curl('curl -H "NoColonHere" https://example.com')
        assert result["headers"] == {}

    def test_header_value_with_colon(self):
        result = parse_curl('curl -H "Authorization: Bearer tok:en:123" https://example.com')
        assert result["headers"]["Authorization"] == "Bearer tok:en:123"


class TestParseCurlBody:
    """Body parsing via -d / --data / --data-raw / --data-binary / --json."""

    def test_data_flag(self):
        result = parse_curl('curl -d "name=value" https://example.com')
        assert result["body"] == "name=value"
        assert result["method"] == "POST"  # auto-inferred

    def test_data_raw(self):
        result = parse_curl('curl --data-raw \'{"key":"val"}\' https://example.com')
        assert result["body"] == '{"key":"val"}'

    def test_data_binary(self):
        result = parse_curl('curl --data-binary "@file.bin" https://example.com')
        assert result["body"] == "@file.bin"

    def test_data_ascii(self):
        result = parse_curl('curl --data-ascii "hello" https://example.com')
        assert result["body"] == "hello"

    def test_json_flag(self):
        result = parse_curl('curl --json \'{"a":1}\' https://example.com')
        assert result["body"] == '{"a":1}'
        assert result["headers"]["Content-Type"] == "application/json"
        assert result["headers"]["Accept"] == "application/json"

    def test_json_does_not_override_existing_content_type(self):
        cmd = 'curl -H "Content-Type: text/plain" --json \'{"a":1}\' https://example.com'
        result = parse_curl(cmd)
        # setdefault → original header preserved
        assert result["headers"]["Content-Type"] == "text/plain"

    def test_explicit_get_with_body(self):
        result = parse_curl('curl -X GET -d "q=test" https://example.com')
        assert result["method"] == "GET"
        assert result["body"] == "q=test"


class TestParseCurlAuth:
    """Basic auth via -u / --user."""

    def test_basic_auth_short(self):
        result = parse_curl("curl -u admin:secret https://example.com")
        expected = base64.b64encode(b"admin:secret").decode()
        assert result["headers"]["Authorization"] == f"Basic {expected}"

    def test_basic_auth_long(self):
        result = parse_curl("curl --user user:pass https://example.com")
        expected = base64.b64encode(b"user:pass").decode()
        assert result["headers"]["Authorization"] == f"Basic {expected}"


class TestParseCurlSSL:
    """-k / --insecure flag."""

    def test_insecure_short(self):
        result = parse_curl("curl -k https://example.com")
        assert result["verify_ssl"] is False

    def test_insecure_long(self):
        result = parse_curl("curl --insecure https://example.com")
        assert result["verify_ssl"] is False

    def test_default_ssl_on(self):
        result = parse_curl("curl https://example.com")
        assert result["verify_ssl"] is True


class TestParseCurlForceGet:
    """-G / --get forces GET method."""

    def test_force_get_with_data(self):
        result = parse_curl('curl -G -d "q=test" https://example.com')
        assert result["method"] == "GET"

    def test_force_get_long(self):
        result = parse_curl('curl --get -d "q=hello" https://example.com')
        assert result["method"] == "GET"


class TestParseCurlContinuation:
    """Multi-line commands with \\ or ^ continuation."""

    def test_unix_continuation(self):
        cmd = "curl \\\n  -X POST \\\n  https://example.com"
        result = parse_curl(cmd)
        assert result["method"] == "POST"
        assert result["url"] == "https://example.com"

    def test_windows_continuation(self):
        cmd = "curl ^\n  -X PUT ^\n  https://example.com"
        result = parse_curl(cmd)
        assert result["method"] == "PUT"
        assert result["url"] == "https://example.com"


class TestParseCurlUnknownFlags:
    """Unknown flags are skipped gracefully."""

    def test_location_flag_ignored(self):
        result = parse_curl("curl -L https://example.com")
        assert result["url"] == "https://example.com"

    def test_output_flag_skipped(self):
        result = parse_curl("curl -o output.json https://example.com")
        assert result["url"] == "https://example.com"

    def test_user_agent_skipped(self):
        result = parse_curl('curl -A "Mozilla/5.0" https://example.com')
        assert result["url"] == "https://example.com"

    def test_max_time_skipped(self):
        result = parse_curl("curl -m 30 https://example.com")
        assert result["url"] == "https://example.com"

    def test_proxy_skipped(self):
        result = parse_curl("curl --proxy http://proxy:8080 https://example.com")
        assert result["url"] == "https://example.com"

    def test_unknown_single_flag_no_arg(self):
        result = parse_curl("curl -v https://example.com")
        assert result["url"] == "https://example.com"

    def test_fallback_split_on_bad_quoting(self):
        # shlex.split will fail on unmatched quote → fallback to naive split
        cmd = 'curl "https://example.com'
        result = parse_curl(cmd)
        # After fallback split the first token with " is the URL
        assert "example.com" in result["url"]


class TestParseCurlComplex:
    """Combined flags — realistic curl commands."""

    def test_full_post(self):
        cmd = (
            "curl -X POST "
            '-H "Content-Type: application/json" '
            '-H "Authorization: Bearer tok123" '
            '-d \'{"name":"test"}\' '
            "https://api.example.com/users"
        )
        result = parse_curl(cmd)
        assert result["method"] == "POST"
        assert result["url"] == "https://api.example.com/users"
        assert result["headers"]["Content-Type"] == "application/json"
        assert result["headers"]["Authorization"] == "Bearer tok123"
        assert result["body"] == '{"name":"test"}'
        assert result["verify_ssl"] is True

