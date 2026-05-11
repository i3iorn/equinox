import pytest
import socket

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


def test_ssrf_dns_failure_is_blocked_by_default(monkeypatch):
    _reset_dns_cache()
    monkeypatch.delenv("EQUINOX_SSRF_ALLOW_ON_DNS_FAILURE", raising=False)

    def _raise_dns(*_args, **_kwargs):
        raise socket.gaierror("dns down")

    monkeypatch.setattr("equinox.core.validation._ssrf.socket.getaddrinfo", _raise_dns)

    with pytest.raises(ValidationError, match="could not be resolved safely"):
        _SsrfGuard.check("unresolved.example")


def test_ssrf_dns_failure_can_be_allowed_by_compat_flag(monkeypatch):
    _reset_dns_cache()
    monkeypatch.setenv("EQUINOX_SSRF_ALLOW_ON_DNS_FAILURE", "1")

    def _raise_dns(*_args, **_kwargs):
        raise socket.gaierror("dns down")

    monkeypatch.setattr("equinox.core.validation._ssrf.socket.getaddrinfo", _raise_dns)

    _SsrfGuard.check("unresolved.example")


