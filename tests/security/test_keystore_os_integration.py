import os
import sys
import types
from typing import Any, cast

import pytest


def test_os_keyring_integration_monkeypatched(monkeypatch: pytest.MonkeyPatch) -> None:
    # Create a fake in-memory keyring module
    store: dict[tuple[str, str], str] = {}
    fake = types.ModuleType("keyring")

    def _get_password(service: str, account: str) -> str | None:
        return store.get((service, account))

    def _set_password(service: str, account: str, password: str) -> None:
        store[(service, account)] = password

    fake_obj = fake  # narrow module type for dynamic attribute assignment in tests
    setattr(cast(Any, fake_obj), "get_password", _get_password)
    setattr(cast(Any, fake_obj), "set_password", _set_password)
    sys.modules["keyring"] = fake

    # Enable OS keyring usage
    os.environ["EQUINOX_USE_OS_KEYRING"] = "1"

    # Import after monkeypatching to ensure the OS path is exercised
    from equinox.security.crypto import get_or_create_raw_key

    k1 = get_or_create_raw_key(None)
    assert isinstance(k1, (bytes, bytearray))
    assert len(k1) == 32

    k2 = get_or_create_raw_key(None)
    assert k1 == k2
