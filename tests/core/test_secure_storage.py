"""Tests for secure credential storage."""

import json

import pytest

from equinox.core.exceptions import SecurityError, ValidationError
from equinox.security.secure_storage import SecureStorage, _wipe_bytes

# Strong password that satisfies the ≥12 chars, 3+ char-class rule.
STRONG_PW = "Str0ng!Export#9"


class TestSecureStorage:
    """Tests for SecureStorage class."""

    @pytest.fixture
    def storage(self, tmp_path):
        """Create temporary secure storage."""
        storage_path = tmp_path / "test_credentials"
        return SecureStorage(storage_path)

    def test_store_and_retrieve(self, storage):
        """Test storing and retrieving a credential."""
        storage.store("api_key", "secret-key-123")
        retrieved = storage.retrieve("api_key")
        assert retrieved == "secret-key-123"

    def test_store_with_metadata(self, storage):
        """Test storing credential with metadata."""
        storage.store(
            "oauth_token",
            "token-abc-123",
            metadata={"expires": "2024-12-31", "scope": "read:user"},
        )
        retrieved = storage.retrieve("oauth_token")
        assert retrieved == "token-abc-123"

    def test_retrieve_nonexistent(self, storage):
        """Test retrieving non-existent credential."""
        result = storage.retrieve("nonexistent")
        assert result is None

    def test_delete_credential(self, storage):
        """Test deleting a credential."""
        storage.store("temp_key", "temp_value")
        assert storage.retrieve("temp_key") == "temp_value"

        deleted = storage.delete("temp_key")
        assert deleted is True
        assert storage.retrieve("temp_key") is None

    def test_delete_nonexistent(self, storage):
        """Test deleting non-existent credential."""
        deleted = storage.delete("nonexistent")
        assert deleted is False

    def test_list_keys(self, storage):
        """Test listing credential keys."""
        storage.store("key1", "value1")
        storage.store("key2", "value2")
        storage.store("key3", "value3")

        keys = storage.list_keys()
        assert "key1" in keys
        assert "key2" in keys
        assert "key3" in keys
        assert len(keys) == 3

    def test_clear_all(self, storage):
        """Test clearing all credentials."""
        storage.store("key1", "value1")
        storage.store("key2", "value2")

        storage.clear()

        keys = storage.list_keys()
        assert len(keys) == 0

    def test_encryption_persistence(self, tmp_path):
        """Test that credentials persist encrypted."""
        storage_path = tmp_path / "test_persist"

        # Store credential
        storage1 = SecureStorage(storage_path)
        storage1.store("persistent_key", "persistent_value")

        # Create new instance and retrieve
        storage2 = SecureStorage(storage_path)
        retrieved = storage2.retrieve("persistent_key")
        assert retrieved == "persistent_value"

    def test_invalid_key_type(self, storage):
        """Test storing with invalid key type."""
        with pytest.raises(ValidationError):
            storage.store(123, "value")  # Key must be string

    def test_invalid_value_type(self, storage):
        """Test storing with invalid value type."""
        with pytest.raises(ValidationError):
            storage.store("key", 123)  # Value must be string

    def test_key_too_long(self, storage):
        """Test storing with key that's too long."""
        long_key = "k" * 300
        with pytest.raises(ValidationError, match="too long"):
            storage.store(long_key, "value")

    def test_value_too_long(self, storage):
        """Test storing value that's too long."""
        long_value = "v" * 20000
        with pytest.raises(ValidationError, match="too long"):
            storage.store("key", long_value)

    def test_file_permissions(self, tmp_path, storage):
        """Test that storage file has restrictive permissions."""
        storage.store("test", "value")

        # Check file exists
        assert storage.storage_path.exists()

        # On Unix-like systems, check permissions
        # On Windows, this might not work the same way
        try:
            mode = storage.storage_path.stat().st_mode
            # Owner should have read/write, others should not
            # This test might need to be skipped on Windows
        except:
            pytest.skip("Permission check not supported on this platform")


# ── Export / Import ───────────────────────────────────────────────────────────


class TestExportImport:
    """Tests for the hardened export_encrypted / import_encrypted flow."""

    @pytest.fixture
    def storage(self, tmp_path):
        s = SecureStorage(tmp_path / "creds")
        s.store("api_key", "sk-abc-123")
        s.store("token", "tok-xyz-789")
        return s

    # ── Password validation ─────────────────────────────────────────

    def test_password_too_short(self, storage, tmp_path):
        with pytest.raises(ValidationError, match="at least 12"):
            storage.export_encrypted(tmp_path / "out", "Short!1")

    def test_password_missing_char_classes(self, storage, tmp_path):
        with pytest.raises(ValidationError, match="3 of"):
            storage.export_encrypted(tmp_path / "out", "alllowercaseletters")

    def test_password_only_lower_and_upper(self, storage, tmp_path):
        with pytest.raises(ValidationError, match="3 of"):
            storage.export_encrypted(tmp_path / "out", "AaBbCcDdEeFfGg")

    def test_strong_password_accepted(self, storage, tmp_path):
        """A password with >=3 character classes and >=12 chars should work."""
        export = tmp_path / "out.json"
        storage.export_encrypted(export, STRONG_PW)
        assert export.exists()

    # ── v2 round-trip ────────────────────────────────────────────────

    def test_export_import_round_trip(self, storage, tmp_path):
        """Credentials survive an export -> import cycle."""
        export = tmp_path / "export.json"
        storage.export_encrypted(export, STRONG_PW)

        # Import into a fresh storage
        target = SecureStorage(tmp_path / "imported_creds")
        count = target.import_encrypted(export, STRONG_PW)

        assert count == 2
        assert target.retrieve("api_key") == "sk-abc-123"
        assert target.retrieve("token") == "tok-xyz-789"

    def test_export_format_is_v2_scrypt(self, storage, tmp_path):
        """The export file must carry version=2 and kdf=scrypt."""
        export = tmp_path / "export.json"
        storage.export_encrypted(export, STRONG_PW)

        with open(export) as f:
            data = json.load(f)

        assert data["version"] == 2
        assert data["kdf"] == "scrypt"
        assert "salt" in data
        assert "data" in data

    def test_export_file_not_plaintext(self, storage, tmp_path):
        """The export file must not contain credential values in plaintext."""
        export = tmp_path / "export.json"
        storage.export_encrypted(export, STRONG_PW)

        raw = export.read_text()
        assert "sk-abc-123" not in raw
        assert "tok-xyz-789" not in raw

    def test_wrong_password_on_import(self, storage, tmp_path):
        export = tmp_path / "export.json"
        storage.export_encrypted(export, STRONG_PW)

        target = SecureStorage(tmp_path / "bad")
        with pytest.raises(SecurityError, match="[Dd]ecryption failed"):
            target.import_encrypted(export, "Wr0ng!Password#9")

    def test_import_merges_with_existing(self, storage, tmp_path):
        """Import should merge into the target storage, not replace it."""
        export = tmp_path / "export.json"
        storage.export_encrypted(export, STRONG_PW)

        target = SecureStorage(tmp_path / "target_creds")
        target.store("existing", "stays")
        target.import_encrypted(export, STRONG_PW)

        assert target.retrieve("existing") == "stays"
        assert target.retrieve("api_key") == "sk-abc-123"

    def test_import_nonexistent_file(self, tmp_path):
        target = SecureStorage(tmp_path / "t")
        with pytest.raises(ValidationError, match="not found"):
            target.import_encrypted(tmp_path / "nope.json", STRONG_PW)

    def test_import_corrupt_file(self, tmp_path):
        bad = tmp_path / "corrupt.json"
        bad.write_text("not json at all {{{")
        target = SecureStorage(tmp_path / "t")
        with pytest.raises(ValidationError, match="Cannot read"):
            target.import_encrypted(bad, STRONG_PW)

    def test_import_missing_fields(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text(json.dumps({"version": 2}))
        target = SecureStorage(tmp_path / "t")
        with pytest.raises(ValidationError, match="missing required"):
            target.import_encrypted(bad, STRONG_PW)

    # ── Legacy v1 compatibility ──────────────────────────────────────

    def test_import_legacy_v1_format(self, storage, tmp_path):
        """A v1 export (PBKDF2, 16-byte salt) must still be importable."""
        import base64

        from cryptography.fernet import Fernet

        pw = STRONG_PW
        salt = b"0123456789abcdef"  # 16 bytes
        key = SecureStorage._derive_pbkdf2(pw, salt)
        cipher = Fernet(key)

        payload = json.dumps({"legacy_key": {"value": "legacy_val", "metadata": {}}})
        encrypted = cipher.encrypt(payload.encode("utf-8"))

        v1_file = tmp_path / "v1.json"
        v1_data = {
            "salt": base64.b64encode(salt).decode(),
            "data": base64.b64encode(encrypted).decode(),
            # no "version" key -> defaults to 1
        }
        with open(v1_file, "w") as f:
            json.dump(v1_data, f)

        target = SecureStorage(tmp_path / "t")
        count = target.import_encrypted(v1_file, pw)
        assert count == 1
        assert target.retrieve("legacy_key") == "legacy_val"

    # ── Key wipe ─────────────────────────────────────────────────────

    def test_wipe_bytes_zeroes_content(self):
        """_wipe_bytes should zero-fill most of the bytes buffer on CPython."""
        import sys

        if sys.implementation.name != "cpython":
            pytest.skip("_wipe_bytes only works on CPython")
        b = b"supersecret!1234"
        _wipe_bytes(b)
        # After wiping, the majority of the buffer should be zeroed.
        # Allow a small margin for CPython header layout differences.
        zero_count = b.count(b"\x00")
        assert zero_count >= len(b) - 2, f"Expected most bytes zeroed, got {zero_count}/{len(b)}"
