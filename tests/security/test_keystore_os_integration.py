import sys
import types

import os

import pytest


def test_os_keyring_integration_monkeypatched(monkeypatch):
    # Create a fake in-memory keyring module
    store = {}
    fake = types.ModuleType("keyring")

    def _get_password(service, account):
        return store.get((service, account))

    def _set_password(service, account, password):
        store[(service, account)] = password

    fake.get_password = _get_password
    fake.set_password = _set_password
    sys.modules["keyring"] = fake

    # Enable OS keyring usage
    os.environ["EQUINOX_USE_OS_KEYRING"] = "1"

    # Import after monkeypatching to ensure the OS path is exercised
    from equinox.core.crypto import get_or_create_raw_key

    k1 = get_or_create_raw_key(None)
    assert isinstance(k1, (bytes, bytearray))
    assert len(k1) == 32

    k2 = get_or_create_raw_key(None)
    assert k1 == k2
