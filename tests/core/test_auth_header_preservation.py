import pytest
from equinox.core.client import HTTPClient
from equinox.core.request import Request
from equinox.auth.base import AuthStrategy

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

def test_auth_header_preserved_in_sent_headers():
    client = HTTPClient()
    request = Request(
        method="GET",
        url="https://httpbin.org/get",
        auth=DummyAuth(),
    )
    response = client.send(request)
    # The sent_headers should include the Authorization header
    assert response.sent_headers is not None
    assert "Authorization" in response.sent_headers
    assert response.sent_headers["Authorization"] == "Bearer test-token"
    # The response panel should display this header (UI test not included here)
