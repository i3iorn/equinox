import httpx
import pytest

from equinox.auth import AuthStrategy
from equinox.core.client import HTTPClient
from equinox.core.client.dispatcher import HttpxDispatcher
from equinox.core.request import Request


class DummyAuth(AuthStrategy):
    AUTH_TYPE = "dummy"
    DISPLAY_NAME = "Dummy Auth"

    def apply(self, request, headers):
        headers["Authorization"] = "Bearer test-token"

    def to_dict(self):
        return {"type": self.AUTH_TYPE}

    @classmethod
    def from_dict(cls, data):
        return cls()


def _mock_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={}, request=request)


def test_auth_header_preserved_in_sent_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_client = httpx.Client(transport=httpx.MockTransport(_mock_handler))
    monkeypatch.setattr(
        HttpxDispatcher,
        "_ensure_client",
        lambda self, verify_ssl=True: mock_client,
    )

    client = HTTPClient()
    request = Request(
        method="GET",
        url="https://httpbin.org/get",
        auth=DummyAuth(),
        verify_ssl=False,
    )
    response = client.send(request)

    assert response.sent_headers is not None
    assert "Authorization" in response.sent_headers
    assert response.sent_headers["Authorization"] == "[REDACTED]"
