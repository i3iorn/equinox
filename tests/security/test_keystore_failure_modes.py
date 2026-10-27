"""The OS keyring must never be replaced just because it could not be read.

``get_or_create_os_key`` used to generate a fresh key on *any* failure, so a
transient keychain error (locked store, D-Bus hiccup) silently overwrote the
stored key and orphaned everything encrypted under it. These tests pin the
distinction the fix introduces:

* a backend that exists but errors  -> raise, never generate;
* a machine with no backend at all  -> "unavailable", fall back to the file key;
* the feature turned off            -> "unavailable".

They monkeypatch the module-level ``keyring`` binding directly rather than
``sys.modules`` so no import-order or module-reload games are needed.
"""

from __future__ import annotations

import base64
import os
import types

import pytest

from equinox.security import keystore


def _roundtrip_key() -> str:
    return base64.b64encode(keystore.KEY_SIZE * b"k").decode("ascii")


def _corrupt_roundtrip_key(garbage: str) -> str:
    """A valid key with one character replaced by a non-alphabet char."""
    key = _roundtrip_key()
    return key[:10] + garbage + key[11:]


class _BackendError(Exception):
    """Stand-in for an arbitrary keyring backend failure."""


def _install_fake_keyring(
    monkeypatch: pytest.MonkeyPatch,
    *,
    raises: Exception | None = None,
    errors: object = None,
) -> None:
    failure = raises if raises is not None else _BackendError("locked")

    def _get_password(_service: str, _account: str) -> str | None:
        raise failure

    fake = types.SimpleNamespace(
        get_password=_get_password,
        set_password=lambda _service, _account, _password: None,
    )
    if errors is not None:
        fake.errors = errors  # type: ignore[attr-defined]
    monkeypatch.setattr(keystore, "keyring", fake)
    monkeypatch.setattr(keystore, "_env_os_keystore_enabled", lambda: True)
    monkeypatch.setattr(keystore, "_os_keyring_available", lambda: True)


def test_backend_error_raises_instead_of_replacing_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A locked/failed keychain must abort — not mint a replacement key."""
    _install_fake_keyring(monkeypatch)

    with pytest.raises(keystore.KeystoreUnavailableError):
        keystore.get_from_os_store()

    # The critical assertion: no replacement key was generated or stored.
    with pytest.raises(keystore.KeystoreUnavailableError):
        keystore.get_or_create_os_key()


def test_missing_backend_falls_back_to_file_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """No backend at all is "unavailable", which the docstring promises falls back."""

    class NoKeyringError(Exception):
        pass

    _install_fake_keyring(
        monkeypatch,
        raises=NoKeyringError("no backend"),
        errors=types.SimpleNamespace(NoKeyringError=NoKeyringError),
    )

    assert keystore.get_from_os_store() is None
    assert keystore.get_or_create_os_key() is None


def test_disabled_feature_never_touches_keyring(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_keyring(monkeypatch)
    monkeypatch.setattr(keystore, "_env_os_keystore_enabled", lambda: False)

    assert keystore.get_from_os_store() is None
    assert keystore.get_or_create_os_key() is None


def test_corrupt_stored_value_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """A value that is not valid base64 is corruption, not "no key"."""
    fake = types.SimpleNamespace(
        get_password=lambda _service, _account: "A",
        set_password=lambda _service, _account, _password: None,
    )
    monkeypatch.setattr(keystore, "keyring", fake)
    monkeypatch.setattr(keystore, "_env_os_keystore_enabled", lambda: True)
    monkeypatch.setattr(keystore, "_os_keyring_available", lambda: True)

    with pytest.raises(keystore.KeystoreUnavailableError):
        keystore.get_from_os_store()


# ── Key validation guards (issue #68) ────────────────────────────────────────


@pytest.mark.parametrize("garbage", ["!", " ", "\n"])
def test_partially_corrupt_base64_fails_loud(monkeypatch: pytest.MonkeyPatch, garbage: str) -> None:
    """Without ``validate=True``, b64decode silently discards non-alphabet
    characters and a partially corrupted stored key decodes to a *wrong*
    32-byte key instead of failing. Strict decoding must reject it."""
    fake = types.SimpleNamespace(
        get_password=lambda _s, _a: _corrupt_roundtrip_key(garbage),
        set_password=lambda _s, _a, _p: None,
    )
    monkeypatch.setattr(keystore, "keyring", fake)
    monkeypatch.setattr(keystore, "_env_os_keystore_enabled", lambda: True)
    monkeypatch.setattr(keystore, "_os_keyring_available", lambda: True)

    with pytest.raises(keystore.KeystoreUnavailableError, match="corrupt"):
        keystore.get_from_os_store()


def test_truncated_key_fails_loud(monkeypatch: pytest.MonkeyPatch) -> None:
    """A decode yielding the wrong byte count is corruption; handing it to
    Fernet would fail later with an undiagnosable InvalidToken."""
    fake = types.SimpleNamespace(
        get_password=lambda _s, _a: base64.b64encode(b"short").decode("ascii"),
        set_password=lambda _s, _a, _p: None,
    )
    monkeypatch.setattr(keystore, "keyring", fake)
    monkeypatch.setattr(keystore, "_env_os_keystore_enabled", lambda: True)
    monkeypatch.setattr(keystore, "_os_keyring_available", lambda: True)

    with pytest.raises(keystore.KeystoreUnavailableError, match="expected 32 bytes"):
        keystore.get_from_os_store()


def test_all_zero_key_fails_loud(monkeypatch: pytest.MonkeyPatch) -> None:
    """A wiped store value decoding to all-zero bytes must fail, not yield a
    deterministic "key" that silently decrypts nothing."""
    fake = types.SimpleNamespace(
        get_password=lambda _s, _a: base64.b64encode(b"\x00" * keystore.KEY_SIZE).decode("ascii"),
        set_password=lambda _s, _a, _p: None,
    )
    monkeypatch.setattr(keystore, "keyring", fake)
    monkeypatch.setattr(keystore, "_env_os_keystore_enabled", lambda: True)
    monkeypatch.setattr(keystore, "_os_keyring_available", lambda: True)

    with pytest.raises(keystore.KeystoreUnavailableError, match="all-zero"):
        keystore.get_from_os_store()


def test_valid_key_roundtrips_through_strict_decode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Strict validation must not reject keys written by this app itself."""
    fake = types.SimpleNamespace(
        get_password=lambda _s, _a: _roundtrip_key(),
        set_password=lambda _s, _a, _p: None,
    )
    monkeypatch.setattr(keystore, "keyring", fake)
    monkeypatch.setattr(keystore, "_env_os_keystore_enabled", lambda: True)
    monkeypatch.setattr(keystore, "_os_keyring_available", lambda: True)

    assert keystore.get_from_os_store() == keystore.KEY_SIZE * b"k"


def test_write_failure_refuses_to_use_unpersisted_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A backend that reads fine but fails writes must not yield a key.

    Run 1 would mint K1, fail to persist it, and encrypt everything under it;
    run 2 would mint K2 and orphan all of run 1's ciphertext. Fail loud
    instead (issue #63).
    """
    fake = types.SimpleNamespace(
        get_password=lambda _service, _account: None,  # store reads empty
        set_password=lambda _service, _account, _password: (_ for _ in ()).throw(
            _BackendError("keychain is read-only"),
        ),
    )
    monkeypatch.setattr(keystore, "keyring", fake)
    monkeypatch.setattr(keystore, "_env_os_keystore_enabled", lambda: True)
    monkeypatch.setattr(keystore, "_os_keyring_available", lambda: True)

    with pytest.raises(keystore.KeystoreUnavailableError, match="could not be stored"):
        keystore.get_or_create_os_key()


def test_partially_corrupt_value_raises_not_wrong_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-alphabet characters in the stored value must fail loudly.

    Without ``b64decode(validate=True)`` the corrupt characters are silently
    discarded and a *wrong 32-byte key* is returned, surfacing later as
    undiagnosable Fernet InvalidToken failures (issue #68).
    """
    import base64

    good_key = os.urandom(32)
    corrupt_b64 = base64.b64encode(good_key).decode("ascii")[:-4] + "!!??"
    fake = types.SimpleNamespace(
        get_password=lambda _service, _account: corrupt_b64,
        set_password=lambda _service, _account, _password: None,
    )
    monkeypatch.setattr(keystore, "keyring", fake)
    monkeypatch.setattr(keystore, "_env_os_keystore_enabled", lambda: True)
    monkeypatch.setattr(keystore, "_os_keyring_available", lambda: True)

    with pytest.raises(keystore.KeystoreUnavailableError, match="corrupt"):
        keystore.get_from_os_store()
