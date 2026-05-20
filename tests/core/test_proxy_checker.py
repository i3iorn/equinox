import errno

import pytest

from equinox.core.exceptions import RequestError
from equinox.core.http import proxy


class DummySock:
    def __init__(self):
        self.closed = False

    def setblocking(self, flag):
        pass

    def connect(self, addr):
        raise BlockingIOError()

    def getsockopt(self, level, optname):
        return errno.ECONNREFUSED

    def close(self):
        self.closed = True


def test_check_proxy_reachable_refused(monkeypatch):
    # monkeypatch socket.socket to return DummySock and select to indicate writable

    def fake_socket(*args, **kwargs):
        return DummySock()

    def fake_select(r, w, x, timeout):
        # indicate writable
        return ([], [object()], [])

    monkeypatch.setattr("equinox.core.http.proxy.socket.socket", fake_socket)
    monkeypatch.setattr("equinox.core.http.proxy._select.select", fake_select)

    with pytest.raises(RequestError):
        proxy.check_proxy_reachable("http://localhost:9999")
