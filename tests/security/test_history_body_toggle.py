import pytest

from equinox.core import history_config
from equinox.core.request.request import Request
from equinox.storage.history._serializer import _HistorySerializer


@pytest.fixture(autouse=True)
def _reset_history_capture_toggle():
    """Keep process-global history capture mode isolated per test."""
    history_config.set_capture_bodies(True)
    yield
    history_config.reset_capture_bodies()


def test_history_body_capture_toggle_off(monkeypatch):
    # Turn off body capture via config
    history_config.set_capture_bodies(False)

    req = Request(
        method="POST",
        url="https://example.com/api/echo",
        headers={},
        body='{"secret": "abc"}',
    )
    s = _HistorySerializer()
    req_row = s.prepare_request(req)
    # When capture_bodies is off, body should be None in serialized row
    assert req_row["body"] is None

    # Also verify that prepare_response respects the toggle
    resp_row = s.prepare_response(None)
    assert resp_row["body"] is None


def test_history_capture_reset_reloads_from_env(monkeypatch):
    history_config.set_capture_bodies(False)
    assert history_config.should_capture_bodies() is False

    monkeypatch.setenv("EQUINOX_HISTORY_CAPTURE_BODIES", "true")
    history_config.reset_capture_bodies()

    assert history_config.should_capture_bodies() is True
