"""Fernet cache precedence in :mod:`equinox.security.auth_cipher`.

``get_fernet`` keeps one process-wide instance. Two bugs lived in that cache:

1. A file-key instance cached early in startup shadowed every later call that
   passed a master-password loader, so auth data kept being encrypted under
   ``~/.equinox/.key`` even when a master password was configured.
2. A loader that raised was indistinguishable from one that returned ``None``,
   so a broken keyring or a cancelled prompt silently fell back to the file key.

These tests pin the precedence rules and the fail-closed behaviour. They build
Fernet objects directly so they never read or create the real key/salt files.
"""

from __future__ import annotations

import logging

import pytest
from cryptography.fernet import Fernet

from equinox.core.exceptions import SecurityError
from equinox.security import auth_cipher


def _reset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth_cipher, "_fernet", None)
    monkeypatch.setattr(auth_cipher, "_fernet_from_master_password", False)


def test_master_password_loader_overrides_cached_file_key(monkeypatch):
    """A cached file-key Fernet must not shadow a provided loader."""
    _reset(monkeypatch)
    monkeypatch.setattr(auth_cipher, "_fernet", Fernet(Fernet.generate_key()))

    master_fernet = Fernet(Fernet.generate_key())
    result = auth_cipher.get_fernet(master_password_loader=lambda: master_fernet)

    assert result is master_fernet
    assert auth_cipher._fernet_from_master_password is True


def test_cached_master_password_fernet_wins_over_later_loader(monkeypatch):
    """Once a master-password Fernet is cached it is reused, not re-derived."""
    _reset(monkeypatch)
    master_fernet = Fernet(Fernet.generate_key())
    monkeypatch.setattr(auth_cipher, "_fernet", master_fernet)
    monkeypatch.setattr(auth_cipher, "_fernet_from_master_password", True)

    calls = {"count": 0}

    def _loader() -> Fernet:
        calls["count"] += 1
        return Fernet(Fernet.generate_key())

    assert auth_cipher.get_fernet(master_password_loader=_loader) is master_fernet
    assert calls["count"] == 0


def test_loader_returning_none_keeps_file_key_fallback(monkeypatch):
    """No master password configured is a normal fallback, not an error."""
    _reset(monkeypatch)
    file_key_fernet = Fernet(Fernet.generate_key())
    monkeypatch.setattr(auth_cipher, "_fernet", file_key_fernet)

    result = auth_cipher.get_fernet(master_password_loader=lambda: None)

    assert result is file_key_fernet
    assert auth_cipher._fernet_from_master_password is False


def test_loader_failure_is_surfaced(monkeypatch):
    """A raising loader must not be swallowed into a silent file-key fallback."""
    _reset(monkeypatch)

    def _broken_loader() -> Fernet:
        raise RuntimeError("keyring unavailable")

    with pytest.raises(SecurityError):
        auth_cipher.get_fernet(master_password_loader=_broken_loader)

    # Nothing may be cached, or a later call would silently return the file key.
    assert auth_cipher._fernet is None


def test_reset_fernet_cache_clears_master_password_flag(monkeypatch):
    _reset(monkeypatch)
    monkeypatch.setattr(auth_cipher, "_fernet", Fernet(Fernet.generate_key()))
    monkeypatch.setattr(auth_cipher, "_fernet_from_master_password", True)

    auth_cipher.reset_fernet_cache()

    assert auth_cipher._fernet is None
    assert auth_cipher._fernet_from_master_password is False


def test_file_key_fallback_warns_at_warning_level(monkeypatch, caplog):
    """Encryption without a master password must be visible, not silent.

    Falling back to the file key (~/.equinox/.key beside the DB) is a real
    strength degradation; the issue requires a WARNING-level notice, not a
    debug line (issue #66).
    """
    _reset(monkeypatch)

    with caplog.at_level("WARNING", logger="equinox.security.auth_cipher"):
        auth_cipher.get_fernet(master_password_loader=lambda: None)

    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings, "file-key fallback must emit a WARNING"
    assert any("WITHOUT a master password" in r.getMessage() for r in warnings)
    assert any(".key" in r.getMessage() for r in warnings)
