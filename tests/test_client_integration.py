"""Integration tests for HTTP client."""

import pytest
from unittest.mock import Mock, patch, MagicMock, PropertyMock
import httpx

from equinox.core.client import HTTPClient
from equinox.core.request import Request
from equinox.core.exceptions import (
    ValidationError,
    RateLimitError,
    TimeoutError,
    CertificateError,
    RequestError
)
from equinox.auth import BearerAuth


class TestHTTPClientIntegration:
    """Integration tests for HTTPClient."""

    def test_client_initialization(self):
        """Test client initialization with various configs."""
        client = HTTPClient(
            timeout=60.0,
            verify_ssl=True,
            max_rate_per_minute=100,
            max_concurrent_requests=5
        )

        assert client.timeout == 60.0
        assert client.verify_ssl is True
        assert client.max_rate_per_minute == 100
        assert client.max_concurrent_requests == 5

    def test_client_default_values(self):
        """Test client default configuration."""
        client = HTTPClient()

        assert client.timeout == HTTPClient.DEFAULT_TIMEOUT
        assert client.verify_ssl is True
        assert client.max_rate_per_minute == 60
        assert client.max_concurrent_requests == 10

    def test_client_timeout_clamping(self):
        """Test that timeout is clamped to valid range."""
        client_low = HTTPClient(timeout=0.01)
        assert client_low.timeout >= HTTPClient.MIN_TIMEOUT

        client_high = HTTPClient(timeout=10000)
        assert client_high.timeout <= HTTPClient.MAX_TIMEOUT

    def test_request_validation(self):
        """Test that requests are validated."""
        client = HTTPClient()

        # Invalid URL
        with pytest.raises(ValidationError):
            request = Request(method="GET", url="not-a-url")
            client.send(request)

        # Invalid method
        with pytest.raises(ValidationError):
            request = Request(method="INVALID", url="https://example.com")
            client.send(request)

    def test_rate_limiting_enforcement(self):
        """Test rate limiting is enforced."""
        client = HTTPClient(max_rate_per_minute=3)

        # First 3 requests should succeed
        for i in range(3):
            client._check_rate_limit()

        # 4th should fail
        with pytest.raises(RateLimitError):
            client._check_rate_limit()

    def test_concurrent_limit_enforcement(self):
        """Test concurrent request limit."""
        client = HTTPClient(max_concurrent_requests=2)

        # First 2 should succeed
        client._check_concurrent_limit()
        client._check_concurrent_limit()

        # 3rd should fail
        with pytest.raises(RequestError, match="concurrent"):
            client._check_concurrent_limit()

        # Release one
        client._release_concurrent_slot()

        # Should work now
        client._check_concurrent_limit()

    @patch('equinox.core.client.httpx.Client')
    def test_response_time_tracking(self, mock_httpx_client):
        """Test that response time is tracked."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.reason_phrase = "OK"
        mock_response.headers = {}
        mock_response.content = b''

        mock_client_instance = Mock()
        mock_client_instance.request.return_value = mock_response
        mock_httpx_client.return_value.__enter__.return_value = mock_client_instance

        client = HTTPClient()
        request = Request(method="GET", url="https://api.example.com/test")

        response = client.send(request)

        assert response.elapsed >= 0
        assert isinstance(response.elapsed, float)
