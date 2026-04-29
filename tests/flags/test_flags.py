import os

import pytest

from equinox.core.config.flags import is_os_keystore_enabled, is_history_capture_enabled


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
