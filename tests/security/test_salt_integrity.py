"""Salt-file integrity and env-var password warnings (issue #71)."""

from __future__ import annotations

import logging

import pytest

from equinox.core.exceptions import SecurityError
from equinox.security import secrets_password


@pytest.fixture(autouse=True)
def _salt_file(tmp_path, monkeypatch):
    monkeypatch.setattr(secrets_password, "_SALT_FILE", tmp_path / "salt.bin")


def test_valid_salt_length_is_accepted():
    assert secrets_password._read_or_create_salt() == (secrets_password._read_or_create_salt())


def test_truncated_salt_fails_loud():
    secrets_password._SALT_FILE.write_bytes(b"short")
    with pytest.raises(SecurityError, match="corrupt"):
        secrets_password._read_or_create_salt()


def test_oversized_salt_fails_loud():
    secrets_password._SALT_FILE.write_bytes(b"x" * 32)
    with pytest.raises(SecurityError, match="corrupt"):
        secrets_password._read_or_create_salt()


def test_zero_byte_salt_fails_loud():
    secrets_password._SALT_FILE.write_bytes(b"")
    with pytest.raises(SecurityError, match="corrupt"):
        secrets_password._read_or_create_salt()


def test_env_var_password_warns_about_exposure(monkeypatch, caplog):
    monkeypatch.delenv("EQUINOX_MASTER_PASSWORD", raising=False)
    monkeypatch.setattr(secrets_password, "_cached_password", None)
    monkeypatch.setenv("EQUINOX_MASTER_PASSWORD", "test-env-pw")

    with caplog.at_level("WARNING", logger="equinox.security.secrets_password"):
        assert secrets_password.get_master_password() == "test-env-pw"

    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("/proc" in r.getMessage() for r in warnings)
