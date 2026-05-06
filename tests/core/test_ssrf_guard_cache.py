import pytest

from equinox.core.exceptions import ValidationError
from equinox.core.validation._ssrf import _SsrfGuard


def _reset_dns_cache() -> None:
    with _SsrfGuard._dns_cache_lock:
        _SsrfGuard._dns_cache.clear()


def test_ssrf_dns_cache_reuses_public_resolution(monkeypatch):
    _reset_dns_cache()

    calls = {"count": 0}

    def fake_getaddrinfo(host, *_args, **_kwargs):
        calls["count"] += 1
        return [
            (2, 1, 6, "", ("93.184.216.34", 0)),
        ]

    monkeypatch.setattr("equinox.core.validation._ssrf.socket.getaddrinfo", fake_getaddrinfo)

    _SsrfGuard.check("example.com")
    _SsrfGuard.check("example.com")

    assert calls["count"] == 1


def test_ssrf_dns_cache_reuses_private_resolution(monkeypatch):
    _reset_dns_cache()

    calls = {"count": 0}

    def fake_getaddrinfo(host, *_args, **_kwargs):
        calls["count"] += 1
        return [
            (2, 1, 6, "", ("10.0.0.5", 0)),
        ]

    monkeypatch.setattr("equinox.core.validation._ssrf.socket.getaddrinfo", fake_getaddrinfo)

    with pytest.raises(ValidationError, match="private"):
        _SsrfGuard.check("internal.test")

    with pytest.raises(ValidationError, match="private"):
        _SsrfGuard.check("internal.test")

    assert calls["count"] == 1

