"""Tests for core/audit.py — audit logger convenience methods and sanitisation."""

import json
import pytest
from pathlib import Path

from equinox.core.audit import AuditLogger, AuditEventType, AuditLevel


@pytest.fixture
def audit(tmp_path):
    log_path = tmp_path / "audit.log"
    return AuditLogger(log_path=str(log_path))


def _read_events(audit):
    """Read all JSON events written to the audit log file."""
    text = audit.log_path.read_text(encoding="utf-8").strip()
    return [json.loads(line) for line in text.splitlines() if line.strip()]


# ── log_event basics ──────────────────────────────────────────────────────


class TestLogEvent:
    def test_basic_event_written(self, audit):
        audit.log_event(AuditEventType.CONFIG_CHANGED, message="cfg updated")
        events = _read_events(audit)
        assert len(events) == 1
        assert events[0]["event_type"] == "config_changed"
        assert events[0]["message"] == "cfg updated"
        assert events[0]["level"] == "info"
        assert events[0]["user"] == "system"

    def test_event_with_user(self, audit):
        audit.log_event(AuditEventType.CONFIG_CHANGED, user="admin", message="x")
        events = _read_events(audit)
        assert events[0]["user"] == "admin"

    def test_event_with_details(self, audit):
        audit.log_event(
            AuditEventType.SETTINGS_UPDATED,
            details={"key": "theme", "value": "dark"},
        )
        events = _read_events(audit)
        assert events[0]["details"]["key"] == "theme"


# ── _sanitize_details ─────────────────────────────────────────────────────


class TestSanitizeDetails:
    def test_sensitive_keys_redacted(self, audit):
        audit.log_event(
            AuditEventType.CREDENTIAL_STORED,
            details={
                "api_key": "super-secret",
                "password": "hunter2",
                "token": "tok-abc",
                "username": "alice",
            },
        )
        events = _read_events(audit)
        d = events[0]["details"]
        assert d["api_key"] == "[REDACTED]"
        assert d["password"] == "[REDACTED]"
        assert d["token"] == "[REDACTED]"
        assert d["username"] == "alice"

    def test_nested_dict_sanitized(self, audit):
        audit.log_event(
            AuditEventType.CREDENTIAL_STORED,
            details={"auth": {"client_secret": "sec", "scope": "read"}},
        )
        events = _read_events(audit)
        d = events[0]["details"]
        assert d["auth"]["client_secret"] == "[REDACTED]"
        assert d["auth"]["scope"] == "read"

    def test_list_with_dicts_sanitized(self, audit):
        audit.log_event(
            AuditEventType.CREDENTIAL_STORED,
            details={"creds": [{"token": "abc"}, {"name": "ok"}]},
        )
        events = _read_events(audit)
        creds = events[0]["details"]["creds"]
        assert creds[0]["token"] == "[REDACTED]"
        assert creds[1]["name"] == "ok"

    def test_long_string_truncated(self, audit):
        audit.log_event(
            AuditEventType.REQUEST_SENT,
            details={"body": "x" * 300},
        )
        events = _read_events(audit)
        val = events[0]["details"]["body"]
        assert len(val) <= 204  # 200 + "..."
        assert val.endswith("...")


# ── Convenience methods ───────────────────────────────────────────────────


class TestConvenienceMethods:
    def test_log_auth_success(self, audit):
        audit.log_auth_success("bearer", user="alice")
        events = _read_events(audit)
        assert events[0]["event_type"] == "auth_success"
        assert "bearer" in events[0]["message"]

    def test_log_auth_failure(self, audit):
        audit.log_auth_failure("basic", "invalid password", user="bob")
        events = _read_events(audit)
        assert events[0]["event_type"] == "auth_failure"
        assert events[0]["level"] == "warning"

    def test_log_credential_access_store(self, audit):
        audit.log_credential_access("store", "my-key")
        events = _read_events(audit)
        assert events[0]["event_type"] == "credential_stored"

    def test_log_credential_access_retrieve(self, audit):
        audit.log_credential_access("retrieve", "my-key")
        events = _read_events(audit)
        assert events[0]["event_type"] == "credential_retrieved"

    def test_log_credential_access_delete(self, audit):
        audit.log_credential_access("delete", "my-key")
        events = _read_events(audit)
        assert events[0]["event_type"] == "credential_deleted"

    def test_log_credential_access_unknown_fallback(self, audit):
        audit.log_credential_access("rotate", "my-key")
        events = _read_events(audit)
        # Falls back to CREDENTIAL_RETRIEVED
        assert events[0]["event_type"] == "credential_retrieved"

    def test_log_request_success(self, audit):
        audit.log_request("GET", "https://api.example.com/users", status_code=200)
        events = _read_events(audit)
        assert events[0]["event_type"] == "request_sent"
        assert "200" in events[0]["message"]

    def test_log_request_failure(self, audit):
        audit.log_request("POST", "https://api.example.com", error="timeout")
        events = _read_events(audit)
        assert events[0]["event_type"] == "request_failed"
        assert events[0]["level"] == "error"

    def test_log_plugin_loaded(self, audit):
        audit.log_plugin_event("my-plugin", "loaded")
        events = _read_events(audit)
        assert events[0]["event_type"] == "plugin_loaded"

    def test_log_plugin_error(self, audit):
        audit.log_plugin_event("my-plugin", "error", error="crash")
        events = _read_events(audit)
        assert events[0]["event_type"] == "plugin_error"
        assert events[0]["level"] == "error"

    def test_log_plugin_unknown_action(self, audit):
        audit.log_plugin_event("my-plugin", "mystery")
        events = _read_events(audit)
        assert events[0]["event_type"] == "plugin_error"  # fallback

    def test_log_security_violation(self, audit):
        audit.log_security_violation("injection", {"payload": "'; DROP TABLE--"})
        events = _read_events(audit)
        assert events[0]["event_type"] == "injection_attempt"

    def test_log_security_violation_rate_limit(self, audit):
        audit.log_security_violation("rate_limit", {"ip": "1.2.3.4"})
        events = _read_events(audit)
        assert events[0]["event_type"] == "rate_limit_exceeded"

    def test_log_security_violation_unknown(self, audit):
        audit.log_security_violation("unknown_type", {})
        events = _read_events(audit)
        # Falls back to VALIDATION_FAILURE
        assert events[0]["event_type"] == "validation_failure"

    def test_log_file_operation_read(self, audit):
        audit.log_file_operation("read", "/tmp/data.json")
        events = _read_events(audit)
        assert events[0]["event_type"] == "file_read"

    def test_log_file_operation_write(self, audit):
        audit.log_file_operation("write", "/tmp/out.json")
        events = _read_events(audit)
        assert events[0]["event_type"] == "file_write"

    def test_log_file_operation_delete(self, audit):
        audit.log_file_operation("delete", "/tmp/old.json")
        events = _read_events(audit)
        assert events[0]["event_type"] == "file_delete"

    def test_log_file_operation_unknown(self, audit):
        audit.log_file_operation("move", "/tmp/x")
        events = _read_events(audit)
        assert events[0]["event_type"] == "file_read"  # fallback


# ── rotate_log ────────────────────────────────────────────────────────────


class TestRotateLog:
    def test_no_rotation_when_small(self, audit):
        audit.log_event(AuditEventType.CONFIG_CHANGED, message="tiny")
        audit.rotate_log(max_size_mb=10)
        # Original file still exists
        assert audit.log_path.exists()

    def test_no_rotation_when_missing(self, tmp_path):
        # Point to a path where the file genuinely doesn't exist (no logger opened it)
        fake_path = tmp_path / "no_log_here" / "audit.log"
        fake_path.parent.mkdir(parents=True, exist_ok=True)
        al = AuditLogger.__new__(AuditLogger)
        al.log_path = fake_path
        al.rotate_log()  # Should not raise

    def test_rotation_creates_archive(self, audit):
        # Write enough data to exceed the threshold
        for _ in range(200):
            audit.log_event(AuditEventType.REQUEST_SENT, message="x" * 500)
        audit.rotate_log(max_size_mb=0)  # 0 MB forces rotation
        # Either the original was renamed or copy+truncate happened
        parent = audit.log_path.parent
        archives = list(parent.glob("audit_*.log"))
        assert len(archives) >= 1

