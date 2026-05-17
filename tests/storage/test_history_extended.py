"""Extended tests for HistoryManager — covers get_history, delete_history,
clear_history, get_stats, URL/header sanitization, and list_history validation."""

import pytest
from unittest.mock import MagicMock

from equinox.storage.database import Database
from equinox.storage.history import HistoryManager
from equinox.core.request import Request, Response
from equinox.core.exceptions import StorageError, ValidationError, SecurityError


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def db(tmp_path):
    return Database(str(tmp_path / "test.db"))


@pytest.fixture
def mgr(db):
    return HistoryManager(db)


def _req(url="https://api.example.com/v1/users", method="GET", body=None, headers=None):
    return Request(method=method, url=url, headers=headers or {}, params={}, body=body)


def _resp(status=200):
    r = MagicMock(spec=Response)
    r.status_code = status
    r.reason = "OK"
    r.body = b'{"id": 1}'
    r.headers = {"content-type": "application/json"}
    r.elapsed = 0.05
    return r


# ── save_history edge cases ───────────────────────────────────────────────────

class TestSaveHistoryEdgeCases:

    def test_save_with_error_only(self, mgr):
        req = _req()
        hid = mgr.save_history(req, error="Connection refused")
        assert hid >= 1
        row = mgr.get_history(hid)
        assert row["error"] == "Connection refused"
        assert row["status_code"] is None

    def test_save_with_response(self, mgr):
        req = _req(method="POST", body='{"x": 1}')
        resp = _resp(200)
        hid = mgr.save_history(req, response=resp)
        row = mgr.get_history(hid)
        assert row["status_code"] == 200
        assert row["method"] == "POST"

    def test_url_truncated_when_too_long(self, mgr):
        long_url = "https://example.com/" + "a" * 3000
        req = _req(url=long_url)
        hid = mgr.save_history(req)
        row = mgr.get_history(hid)
        assert len(row["url"]) <= HistoryManager.MAX_URL_LENGTH

    def test_url_sanitizes_password_param(self, mgr):
        req = _req(url="https://api.example.com/login?password=secret123&user=alice")
        hid = mgr.save_history(req)
        row = mgr.get_history(hid)
        assert "secret123" not in row["url"]
        assert "[REDACTED]" in row["url"]

    def test_url_sanitizes_token_param(self, mgr):
        req = _req(url="https://api.example.com/data?api_key=supersecret&page=1")
        hid = mgr.save_history(req)
        row = mgr.get_history(hid)
        assert "supersecret" not in row["url"]

    def test_url_sanitizes_auth_param(self, mgr):
        req = _req(url="https://api.example.com/data?authorization=Bearer+tok")
        hid = mgr.save_history(req)
        row = mgr.get_history(hid)
        assert "tok" not in row["url"]

    def test_request_headers_sanitized(self, mgr):
        req = _req(headers={
            "Authorization": "Bearer my-secret",
            "X-Api-Key": "key-value",
            "Content-Type": "application/json",
        })
        hid = mgr.save_history(req)
        row = mgr.get_history(hid)
        assert row["request_headers"]["Authorization"] == "[REDACTED]"
        assert row["request_headers"]["X-Api-Key"] == "[REDACTED]"
        assert row["request_headers"]["Content-Type"] == "application/json"

    def test_response_bytes_body_decoded(self, mgr):
        req = _req()
        resp = _resp()
        resp.body = b'{"ok": true}'
        hid = mgr.save_history(req, response=resp)
        row = mgr.get_history(hid)
        assert "ok" in row["response_body"]

    def test_body_large_error_truncated(self, mgr):
        req = _req()
        long_err = "x" * (HistoryManager.MAX_ERROR_MESSAGE_LENGTH + 100)
        hid = mgr.save_history(req, error=long_err)
        row = mgr.get_history(hid)
        assert row["error"].endswith("[TRUNCATED]")

    def test_request_body_truncated(self, mgr):
        big_body = "a" * (HistoryManager.MAX_BODY_SIZE + 100)
        req = _req(method="POST", body=big_body)
        hid = mgr.save_history(req)
        row = mgr.get_history(hid)
        assert "[TRUNCATED]" in row["request_body"]

    def test_request_correlation_id_persisted(self, mgr):
        req = _req()
        req.correlation_id = "reqabc123456"
        hid = mgr.save_history(req)
        row = mgr.get_history(hid)
        assert row["request_correlation_id"] == "reqabc123456"


# ── get_history validation ────────────────────────────────────────────────────

class TestGetHistory:

    def test_get_existing(self, mgr):
        hid = mgr.save_history(_req())
        row = mgr.get_history(hid)
        assert row is not None
        assert row["id"] == hid

    def test_get_nonexistent_returns_none(self, mgr):
        assert mgr.get_history(9999) is None

    def test_invalid_id_raises(self, mgr):
        with pytest.raises(ValidationError):
            mgr.get_history(0)
        with pytest.raises(ValidationError):
            mgr.get_history(-1)


# ── list_history validation ───────────────────────────────────────────────────

class TestListHistory:

    def test_list_default(self, mgr):
        mgr.save_history(_req())
        mgr.save_history(_req())
        rows = mgr.list_history()
        assert len(rows) >= 2

    def test_list_with_request_id_filter(self, mgr):
        hid = mgr.save_history(_req())
        row = mgr.get_history(hid)
        rows = mgr.list_history(request_id=row.get("request_id") or 1)
        # may return empty if request_id is None, but should not raise
        assert isinstance(rows, list)

    def test_list_invalid_limit_raises(self, mgr):
        with pytest.raises(ValidationError):
            mgr.list_history(limit=0)
        with pytest.raises(ValidationError):
            mgr.list_history(limit=-5)

    def test_list_limit_too_large_raises(self, mgr):
        with pytest.raises(SecurityError):
            mgr.list_history(limit=HistoryManager.MAX_LIMIT + 1)

    def test_list_invalid_offset_raises(self, mgr):
        with pytest.raises(ValidationError):
            mgr.list_history(offset=-1)

    def test_list_invalid_request_id_raises(self, mgr):
        with pytest.raises(ValidationError):
            mgr.list_history(request_id=0)

    def test_list_with_offset(self, mgr):
        for _ in range(5):
            mgr.save_history(_req())
        all_rows = mgr.list_history(limit=10)
        offset_rows = mgr.list_history(limit=10, offset=2)
        assert len(offset_rows) == len(all_rows) - 2


# ── delete_history ────────────────────────────────────────────────────────────

class TestDeleteHistory:

    def test_delete_existing(self, mgr):
        hid = mgr.save_history(_req())
        mgr.delete_history(hid)
        assert mgr.get_history(hid) is None

    def test_delete_nonexistent_raises(self, mgr):
        with pytest.raises(StorageError):
            mgr.delete_history(9999)

    def test_delete_invalid_id_raises(self, mgr):
        with pytest.raises(ValidationError):
            mgr.delete_history(0)


# ── clear_history ─────────────────────────────────────────────────────────────

class TestClearHistory:

    def test_clear_all(self, mgr):
        mgr.save_history(_req())
        mgr.save_history(_req())
        mgr.clear_history()
        assert mgr.list_history() == []

    def test_clear_older_than_days(self, mgr):
        hid = mgr.save_history(_req())
        mgr.clear_history(days=365)  # won't delete our just-saved entry
        assert mgr.get_history(hid) is not None  # still there

    def test_clear_invalid_days_raises(self, mgr):
        with pytest.raises(ValidationError):
            mgr.clear_history(days=0)
        with pytest.raises(ValidationError):
            mgr.clear_history(days=-1)

    def test_clear_days_too_large_raises(self, mgr):
        with pytest.raises(ValidationError):
            mgr.clear_history(days=99999)


# ── get_stats ─────────────────────────────────────────────────────────────────

class TestGetStats:

    def test_stats_empty(self, mgr):
        stats = mgr.get_stats()
        print(stats)
        assert stats["total"] == 0
        assert stats["successful"] == 0
        assert stats["failed"] == 0

    def test_stats_with_successful_responses(self, mgr):
        resp = _resp(200)
        mgr.save_history(_req(), response=resp)
        resp2 = _resp(201)
        mgr.save_history(_req(), response=resp2)
        stats = mgr.get_stats()
        assert stats["total"] == 2
        assert stats["successful"] == 2
        assert stats["failed"] == 0

    def test_stats_with_failed_responses(self, mgr):
        resp_err = _resp(500)
        mgr.save_history(_req(), response=resp_err)
        mgr.save_history(_req(), error="Connection refused")
        stats = mgr.get_stats()
        assert stats["total"] == 2
        assert stats["failed"] == 2


# ── _prepare_url and _prepare_headers (on _HistorySerializer) ──────────────────

class TestSanitizers:

    def test_sanitize_url_clean(self, mgr):
        url = "https://api.example.com/resource?page=1&size=10"
        result = mgr._serializer._prepare_url(url)
        assert result == url

    def test_sanitize_url_redacts_password(self, mgr):
        url = "https://example.com/api?password=hunter2&foo=bar"
        result = mgr._serializer._prepare_url(url)
        assert "hunter2" not in result
        assert "[REDACTED]" in result

    def test_sanitize_url_redacts_token(self, mgr):
        url = "https://example.com/api?token=abc123&other=x"
        result = mgr._serializer._prepare_url(url)
        assert "abc123" not in result

    def test_sanitize_headers_redacts_auth(self, mgr):
        headers = {
            "Authorization": "Bearer secret",
            "cookie": "session=abc",
            "Accept": "application/json",
        }
        result_json = mgr._serializer._prepare_headers(headers)
        import json
        result = json.loads(result_json)
        assert result["Authorization"] == "[REDACTED]"
        assert result["cookie"] == "[REDACTED]"
        assert result["Accept"] == "application/json"

    def test_sanitize_headers_empty(self, mgr):
        import json
        result_json = mgr._serializer._prepare_headers({})
        assert json.loads(result_json) == {}
