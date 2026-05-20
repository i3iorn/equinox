"""Tests for HTTP client"""

import pytest

from equinox.core.client import HTTPClient
from equinox.core.request import Request


def test_create_request():
    """Test creating a request"""
    request = Request(
        method="GET",
        url="https://httpbin.org/get",
        headers={"User-Agent": "Equinox/0.1.0"},
        params={"test": "value"},
    )

    assert request.method == "GET"
    assert request.url == "https://httpbin.org/get"
    assert request.headers["User-Agent"] == "Equinox/0.1.0"
    assert request.params["test"] == "value"


def test_request_to_dict():
    """Test converting request to dictionary"""
    request = Request(
        method="POST",
        url="https://httpbin.org/post",
        body='{"test": "data"}',
        name="Test Request",
    )

    data = request.to_dict()
    assert data["method"] == "POST"
    assert data["url"] == "https://httpbin.org/post"
    assert data["body"] == '{"test": "data"}'
    assert data["name"] == "Test Request"


def test_request_from_dict():
    """Test creating request from dictionary"""
    data = {
        "method": "GET",
        "url": "https://httpbin.org/get",
        "headers": {"Accept": "application/json"},
        "params": {"key": "value"},
    }

    request = Request.from_dict(data)
    assert request.method == "GET"
    assert request.url == "https://httpbin.org/get"
    assert request.headers["Accept"] == "application/json"
    assert request.params["key"] == "value"


def test_request_to_curl():
    """Test converting request to curl command"""
    request = Request(
        method="POST",
        url="https://httpbin.org/post",
        headers={"Content-Type": "application/json"},
        body='{"test": "data"}',
    )

    curl = request.to_curl()
    assert "curl" in curl
    assert "-X POST" in curl
    assert "https://httpbin.org/post" in curl
    assert "Content-Type: application/json" in curl


@pytest.mark.skip(reason="Requires network access")
def test_send_get_request():
    """Test sending GET request"""
    client = HTTPClient()
    request = Request(method="GET", url="https://httpbin.org/get")

    response = client.send(request)

    assert response.status_code == 200
    assert response.is_json
    data = response.json()
    assert "url" in data


@pytest.mark.skip(reason="Requires network access")
def test_send_post_request():
    """Test sending POST request"""
    client = HTTPClient()
    request = Request(
        method="POST",
        url="https://httpbin.org/post",
        headers={"Content-Type": "application/json"},
        body='{"test": "data"}',
    )

    response = client.send(request)

    assert response.status_code == 200
    assert response.is_json


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
