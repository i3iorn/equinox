"""Tests for the ``enc:`` storage-format contract (auth_cipher_storage).

Covers the legacy-plaintext collision from issue #67: a stored value that
starts with ``enc:`` but whose remainder is not Fernet ciphertext is a
literal plaintext secret (e.g. a pasted API key) and must read back as
plaintext, while token-shaped values that fail decryption still fail loudly.
"""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from equinox.core.exceptions import SecurityError
from equinox.security import auth_cipher
from equinox.storage.auth_cipher_storage import (
    decrypt_auth_storage_value,
    encrypt_auth_storage_value,
)


@pytest.fixture(autouse=True)
def _reset_fernet(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(auth_cipher, "_fernet", None)
    monkeypatch.setattr(auth_cipher, "_fernet_from_master_password", False)
    yield
    auth_cipher.reset_fernet_cache()


def test_roundtrip_preserves_value_starting_with_enc_prefix():
    plaintext = "enc:1234-abc"  # a user secret that literally starts with enc:
    stored = encrypt_auth_storage_value(plaintext)
    assert stored.startswith("enc:")
    assert decrypt_auth_storage_value(stored, field_name="test") == plaintext


def test_legacy_plaintext_with_enc_prefix_reads_back_raw():
    """A legacy row containing a literal 'enc:...' plaintext is not ciphertext.

    Decryption would fail with a confusing SecurityError; the raw value must
    be returned instead (issue #67).
    """
    stored = "enc:this-is-a-plaintext-api-key"
    assert decrypt_auth_storage_value(stored, field_name="test") == stored


def test_legacy_plaintext_collision_warns(caplog):
    stored = "enc:not-a-fernet-token"
    with caplog.at_level("WARNING", logger="equinox.storage.auth_cipher_storage"):
        result = decrypt_auth_storage_value(stored, field_name="test")
    assert result == stored
    assert any("legacy plaintext" in r.getMessage() for r in caplog.records)


def test_token_shaped_corruption_still_raises(monkeypatch):
    """A value that IS Fernet-shaped but undecryptable fails loudly."""
    real = Fernet(Fernet.generate_key())
    stored = "enc:" + real.encrypt(b"secret").decode()

    # Cache a different key so decryption must fail with InvalidToken.
    monkeypatch.setattr(auth_cipher, "_fernet", Fernet(Fernet.generate_key()))
    monkeypatch.setattr(auth_cipher, "_fernet_from_master_password", True)

    with pytest.raises(SecurityError, match="invalid or corrupted"):
        decrypt_auth_storage_value(stored, field_name="test")
