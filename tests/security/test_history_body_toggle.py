
import pytest

from equinox.core.history_config import set_capture_bodies
from equinox.storage.history._serializer import _HistorySerializer
from equinox.core.request.request import Request


@pytest.fixture(autouse=True)
def _reset_history_capture_toggle():
    """Keep process-global history capture mode isolated per test."""
    set_capture_bodies(True)
    yield
    set_capture_bodies(True)


def test_history_body_capture_toggle_off(monkeypatch):
    # Turn off body capture via config
    set_capture_bodies(False)

    req = Request(
        method="POST",
        url="https://example.com/api/echo",
        headers={},
        body='{"secret": "abc"}'
    )
    s = _HistorySerializer()
    req_row = s.prepare_request(req)
    # When capture_bodies is off, body should be None in serialized row
    assert req_row["body"] is None

    # Also verify that prepare_response respects the toggle
    resp_row = s.prepare_response(None)
    assert resp_row["body"] is None
