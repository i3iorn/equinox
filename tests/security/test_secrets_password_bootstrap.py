import logging

import pytest

from equinox.security import secrets_password


def test_startup_rotation_failure_logs_and_continues(monkeypatch, caplog):
    monkeypatch.setenv("EQUINOX_DB_PATH", "C:/tmp/equinox.db")
    monkeypatch.setenv("EQUINOX_STRICT_SECRET_ROTATION", "0")
    monkeypatch.setattr(secrets_password, "get_fernet_for_password", lambda password=None: object())
    monkeypatch.setattr(secrets_password, "get_master_password", lambda: "pw")

    def _boom(*_args, **_kwargs):
        raise RuntimeError("rotate failed")

    monkeypatch.setattr(secrets_password, "rotate_all_secrets", _boom)

    with caplog.at_level(logging.ERROR):
        result = secrets_password.ensure_master_password_initialized()

    assert result is not None
    assert "secret_rotation_startup_failed" in caplog.text


def test_startup_rotation_failure_raises_in_strict_mode(monkeypatch):
    monkeypatch.setenv("EQUINOX_DB_PATH", "C:/tmp/equinox.db")
    monkeypatch.setenv("EQUINOX_STRICT_SECRET_ROTATION", "1")
    monkeypatch.setattr(secrets_password, "get_fernet_for_password", lambda password=None: object())
    monkeypatch.setattr(secrets_password, "get_master_password", lambda: "pw")

    def _boom(*_args, **_kwargs):
        raise RuntimeError("rotate failed")

    monkeypatch.setattr(secrets_password, "rotate_all_secrets", _boom)

    with pytest.raises(RuntimeError, match="strict mode"):
        secrets_password.ensure_master_password_initialized()
