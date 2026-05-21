"""Tests for security/serialization.py, security/crypto.py,
core/auth_cipher.py, auth/_basic.py, auth/_bearer.py coverage gaps."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from equinox.auth._basic import BasicAuth
from equinox.auth._bearer import BearerAuth
from equinox.core.exceptions import AuthError
from equinox.security.serialization import serialize_body, serialize_headers

# ── serialize_headers / serialize_body ────────────────────────────────────────


class TestSerializeHeaders:
    def test_normal_headers(self) -> None:
        result = serialize_headers({"Content-Type": "application/json"})
        assert "Content-Type" in result

    def test_none_headers_treated_as_empty(self) -> None:
        result = serialize_headers(None)  # type: ignore[arg-type]
        assert isinstance(result, str)

    def test_sensitive_header_redacted(self) -> None:
        result = serialize_headers({"Authorization": "Bearer secret-token"})
        assert "secret-token" not in result


class TestSerializeBody:
    def test_none_returns_none(self) -> None:
        assert serialize_body(None) is None

    def test_capture_false_returns_none(self) -> None:
        assert serialize_body("some body", capture=False) is None

    def test_string_body_returned(self) -> None:
        assert serialize_body("hello") == "hello"

    def test_non_string_coerced(self) -> None:
        assert serialize_body({"key": "val"}) is not None

    def test_long_body_truncated(self) -> None:
        big = "x" * 200
        result = serialize_body(big, max_len=100)
        assert result is not None
        assert "TRUNCATED" in result
        assert len(result) < 200


# ── auth/_basic.py uncovered lines ───────────────────────────────────────────


class TestBasicAuthCoverage:
    def test_get_preflight_warning_empty_username(self) -> None:
        """Lines 68-70: get_preflight_warning when username manages to be falsy.

        _validate_credential prevents an empty username via __init__, so we
        set the attribute directly to test the warning path.
        """
        auth = BasicAuth("user", "pass")
        auth.username = ""  # bypass validation
        warning = auth.get_preflight_warning()
        assert warning is not None
        assert "empty" in warning.lower()

    def test_get_preflight_warning_normal_user_is_none(self) -> None:
        auth = BasicAuth("alice", "secret")
        assert auth.get_preflight_warning() is None

    def test_repr_short_username(self) -> None:
        """Line 75: len(username) <= 2 → shows '****'."""
        auth = BasicAuth("ab", "p")
        r = repr(auth)
        assert "****" in r

    def test_repr_long_username(self) -> None:
        auth = BasicAuth("alice", "secret")
        r = repr(auth)
        assert "al****" in r

    def test_from_dict_missing_key_raises(self) -> None:
        with pytest.raises(AuthError, match="missing key"):
            BasicAuth.from_dict({"username": "alice"})  # no 'password'


# ── auth/_bearer.py uncovered lines ──────────────────────────────────────────


class TestBearerAuthCoverage:
    def test_get_preflight_warning_empty_token(self) -> None:
        """Lines 69-71: warning returned for empty token (set directly)."""
        auth = BearerAuth("tok")
        auth.token = ""  # bypass validation
        warning = auth.get_preflight_warning()
        assert warning is not None

    def test_get_preflight_warning_valid_is_none(self) -> None:
        auth = BearerAuth("valid-token")
        assert auth.get_preflight_warning() is None

    def test_eq_same_token(self) -> None:
        """Line 77: __eq__ between two BearerAuth instances."""
        a = BearerAuth("token")
        b = BearerAuth("token")
        assert a == b

    def test_eq_different_token(self) -> None:
        a = BearerAuth("token1")
        b = BearerAuth("token2")
        assert a != b

    def test_eq_non_bearer_returns_not_implemented(self) -> None:
        """Line 77: NotImplemented for different type."""
        auth = BearerAuth("tok")
        result = auth.__eq__("not_a_bearer")
        assert result is NotImplemented

    def test_hash_consistent(self) -> None:
        """Line 81: __hash__."""
        a = BearerAuth("token")
        b = BearerAuth("token")
        assert hash(a) == hash(b)

    def test_from_dict_missing_key_raises(self) -> None:
        with pytest.raises(AuthError, match="missing key"):
            BearerAuth.from_dict({})


# ── security/crypto.py uncovered lines ───────────────────────────────────────


class TestCryptoCoverage:
    def test_key_file_valid_absent_path(self, tmp_path: Path) -> None:
        from equinox.security.crypto import key_file_valid

        absent = tmp_path / "nonexistent.key"
        assert not key_file_valid(absent)

    def test_key_file_valid_wrong_size(self, tmp_path: Path) -> None:
        from equinox.security.crypto import key_file_valid

        short = tmp_path / "short.key"
        short.write_bytes(b"tooshort")
        assert not key_file_valid(short)

    def test_key_file_valid_correct_size(self, tmp_path: Path) -> None:
        from equinox.security.crypto import KEY_SIZE, key_file_valid

        key_file = tmp_path / "valid.key"
        key_file.write_bytes(os.urandom(KEY_SIZE))
        assert key_file_valid(key_file)

    def test_get_or_create_raw_key_reads_existing(self, tmp_path: Path) -> None:
        from equinox.security.crypto import KEY_SIZE, get_or_create_raw_key

        key_file = tmp_path / ".key"
        expected = os.urandom(KEY_SIZE)
        key_file.write_bytes(expected)
        # Disable OS keyring so we go to the file path
        with patch("equinox.security.crypto.get_or_create_os_key", return_value=None):
            result = get_or_create_raw_key(key_file)
        assert result == expected

    def test_get_or_create_raw_key_corrupt_raises(self, tmp_path: Path) -> None:
        from equinox.security.crypto import get_or_create_raw_key

        key_file = tmp_path / ".key"
        key_file.write_bytes(b"short")
        with patch("equinox.security.crypto.get_or_create_os_key", return_value=None):
            with pytest.raises(RuntimeError, match="Corrupt"):
                get_or_create_raw_key(key_file)

    def test_make_fernet_wrong_size_raises(self) -> None:
        from equinox.security.crypto import make_fernet

        with pytest.raises(ValueError, match="exactly"):
            make_fernet(b"tooshort")

    def test_make_fernet_correct_size(self) -> None:
        from equinox.security.crypto import KEY_SIZE, make_fernet

        fernet = make_fernet(os.urandom(KEY_SIZE))
        # Verify it can encrypt/decrypt
        token = fernet.encrypt(b"hello")
        assert fernet.decrypt(token) == b"hello"

    def test_get_or_create_fernet_creates_key(self, tmp_path: Path) -> None:
        from equinox.security.crypto import get_or_create_fernet

        key_path = tmp_path / ".key"
        with patch("equinox.security.crypto.get_or_create_os_key", return_value=None):
            fernet = get_or_create_fernet(key_path)
        assert key_path.exists()
        token = fernet.encrypt(b"test")
        assert fernet.decrypt(token) == b"test"


# ── core/auth_cipher.py uncovered lines ──────────────────────────────────────


class TestAuthCipherCoverage:
    def setup_method(self) -> None:
        from equinox.core.auth_cipher import reset_cipher

        reset_cipher()

    def teardown_method(self) -> None:
        from equinox.core.auth_cipher import reset_cipher

        reset_cipher()

    def test_encrypt_and_decrypt_roundtrip(self, tmp_path: Path) -> None:
        from equinox.core.auth_cipher import decrypt_auth_data, encrypt_auth_data
        from equinox.security.crypto import KEY_SIZE

        key_path = tmp_path / ".key"
        key_path.write_bytes(os.urandom(KEY_SIZE))
        with (
            patch("equinox.security.crypto.get_or_create_os_key", return_value=None),
            patch("equinox.security.crypto.default_key_path", return_value=key_path),
            patch("equinox.core.auth_cipher.ensure_master_password_initialized", return_value=None),
        ):
            plaintext = '{"type": "bearer", "token": "abc"}'
            encrypted = encrypt_auth_data(plaintext)
            assert encrypted.startswith("enc:")
            decrypted = decrypt_auth_data(encrypted)
            assert decrypted == plaintext

    def test_decrypt_legacy_plaintext_returned_as_is(self, tmp_path: Path) -> None:
        from equinox.core.auth_cipher import decrypt_auth_data
        from equinox.security.crypto import KEY_SIZE

        key_path = tmp_path / ".key"
        key_path.write_bytes(os.urandom(KEY_SIZE))
        with (
            patch("equinox.security.crypto.get_or_create_os_key", return_value=None),
            patch("equinox.security.crypto.default_key_path", return_value=key_path),
            patch("equinox.core.auth_cipher.ensure_master_password_initialized", return_value=None),
        ):
            legacy = '{"type":"bearer"}'
            assert decrypt_auth_data(legacy) == legacy

    def test_decrypt_empty_returns_empty(self) -> None:
        from equinox.core.auth_cipher import decrypt_auth_data

        assert decrypt_auth_data("") == ""

    def test_decrypt_invalid_token_raises(self, tmp_path: Path) -> None:
        from equinox.core.auth_cipher import decrypt_auth_data
        from equinox.core.exceptions import SecurityError
        from equinox.security.crypto import KEY_SIZE

        key_path = tmp_path / ".key"
        key_path.write_bytes(os.urandom(KEY_SIZE))
        with (
            patch("equinox.security.crypto.get_or_create_os_key", return_value=None),
            patch("equinox.security.crypto.default_key_path", return_value=key_path),
            patch("equinox.core.auth_cipher.ensure_master_password_initialized", return_value=None),
        ):
            with pytest.raises(SecurityError):
                decrypt_auth_data("enc:notvalidbase64==", "my_field")
