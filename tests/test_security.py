"""Security-focused integration tests."""

import pytest
from equinox.core.client import HTTPClient
from equinox.core.request import Request
from equinox.core.exceptions import ValidationError
from equinox.storage.database import Database


class TestSQLInjectionPrevention:
    """Tests to ensure SQL injection is prevented."""

    @pytest.fixture
    def db(self, tmp_path):
        """Create temporary database."""
        db_path = tmp_path / "test.db"
        return Database(str(db_path))

    def test_query_validation_prevents_injection(self, db):
        """Test that parameterized queries prevent SQL injection."""
        # This should fail because we're using direct string interpolation
        # The database should only accept parameterized queries

        # Try to inject SQL via parameter
        malicious_id = "1 OR 1=1"

        # This should be safe because we use parameterized queries
        # In real code, this would be used with proper parameters
        query = "SELECT * FROM collections WHERE id = ?"

        # This should work fine with parameterized query
        result = db.fetchone(query, (malicious_id,))
        # Should return None or handle gracefully

    def test_insert_validation(self, db):
        """Test insert query validation."""
        # Non-INSERT query should fail
        with pytest.raises(ValidationError, match="INSERT"):
            db.insert("SELECT * FROM collections", ())


class TestInputValidation:
    """Tests for input validation against attacks."""

    def test_url_injection_prevention(self):
        """Test that URL validation prevents injection."""
        client = HTTPClient()

        # Test various injection attempts
        malicious_urls = [
            "javascript:alert('XSS')",
            "data:text/html,<script>alert('XSS')</script>",
            "file:///etc/passwd",
        ]

        for url in malicious_urls:
            request = Request(method="GET", url=url)
            with pytest.raises(ValidationError):
                client.send(request)

    def test_header_injection_prevention(self):
        """Test that header validation prevents CRLF injection."""
        client = HTTPClient()

        # Attempt CRLF injection
        malicious_value = "value\r\nX-Injected: evil"

        request = Request(
            method="GET",
            url="https://example.com",
            headers={"X-Custom": malicious_value}
        )

        with pytest.raises(ValidationError, match="CRLF"):
            client.send(request)

    def test_command_injection_in_params(self):
        """Test parameter validation against command injection."""
        client = HTTPClient()

        malicious_params = {
            "cmd": "test; rm -rf /",
            "exec": "$(cat /etc/passwd)",
        }

        request = Request(
            method="GET",
            url="https://example.com",
            params=malicious_params
        )

        with pytest.raises(ValidationError):
            client.send(request)




class TestPathTraversal:
    """Tests for path traversal prevention."""

    def test_path_traversal_prevention(self):
        """Test that path traversal is prevented."""
        from equinox.core.validation import Validator

        malicious_paths = [
            "../../../etc/passwd",
            "..\\..\\..\\windows\\system32\\config\\sam",
            "../../../../root/.ssh/id_rsa",
            "..%2F..%2F..%2Fetc%2Fpasswd",  # URL encoded
        ]

        for path in malicious_paths:
            with pytest.raises(ValidationError, match="traversal"):
                Validator.validate_file_path(path)


class TestDataSanitization:
    """Tests for data sanitization."""

    def test_sensitive_data_redaction(self):
        """Test that sensitive data is redacted in logs."""
        from equinox.core.audit import AuditLogger
        import tempfile

        with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
            log_path = f.name

        logger = AuditLogger(log_path=log_path)

        # Log event with sensitive data
        from equinox.core.audit import AuditEventType

        logger.log_event(
            event_type=AuditEventType.CREDENTIAL_STORED,
            message="Stored credential",
            details={
                "api_key": "secret-key-123",
                "password": "super-secret",
                "token": "bearer-token-xyz",
                "username": "john_doe",  # Not sensitive
            }
        )

        # Read log file
        with open(log_path, 'r') as f:
            log_content = f.read()

        # Sensitive data should be redacted
        assert "secret-key-123" not in log_content
        assert "super-secret" not in log_content
        assert "bearer-token-xyz" not in log_content
        assert "[REDACTED]" in log_content

        # Non-sensitive data should be present
        assert "john_doe" in log_content
