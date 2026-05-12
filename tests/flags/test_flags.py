
import pytest

from equinox.core.config.flags import (
    is_history_capture_enabled,
    is_os_keystore_enabled,
    is_ssrf_allow_on_dns_failure_enabled,
    is_strict_secret_rotation_enabled,
)


@pytest.mark.usefixtures("monkeypatch")
def test_os_keystore_flag(monkeypatch):
    monkeypatch.setenv("EQUINOX_USE_OS_KEYRING", "1")
    assert is_os_keystore_enabled() is True
    monkeypatch.setenv("EQUINOX_USE_OS_KEYRING", "0")
    assert is_os_keystore_enabled() is False


@pytest.mark.usefixtures("monkeypatch")
def test_history_capture_flag(monkeypatch):
    # Default should be enabled
    monkeypatch.delenv("EQUINOX_HISTORY_CAPTURE_BODIES", raising=False)
    assert is_history_capture_enabled() is True

    monkeypatch.setenv("EQUINOX_HISTORY_CAPTURE_BODIES", "0")
    assert is_history_capture_enabled() is False

    monkeypatch.setenv("EQUINOX_HISTORY_CAPTURE_BODIES", "1")
    assert is_history_capture_enabled() is True


@pytest.mark.usefixtures("monkeypatch")
def test_ssrf_dns_failure_flag(monkeypatch):
    monkeypatch.delenv("EQUINOX_SSRF_ALLOW_ON_DNS_FAILURE", raising=False)
    assert is_ssrf_allow_on_dns_failure_enabled() is False

    monkeypatch.setenv("EQUINOX_SSRF_ALLOW_ON_DNS_FAILURE", "1")
    assert is_ssrf_allow_on_dns_failure_enabled() is True


@pytest.mark.usefixtures("monkeypatch")
def test_strict_secret_rotation_flag(monkeypatch):
    monkeypatch.delenv("EQUINOX_STRICT_SECRET_ROTATION", raising=False)
    assert is_strict_secret_rotation_enabled() is False

    monkeypatch.setenv("EQUINOX_STRICT_SECRET_ROTATION", "yes")
    assert is_strict_secret_rotation_enabled() is True

