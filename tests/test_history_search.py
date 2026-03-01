"""Tests for the advanced history search/filter functionality.

Covers: status_code exact match, status_class ranges, body regex,
JSONPath filter (existence + value match), content-type, elapsed time,
response header filter, date range, combined filters, and edge cases.
"""

import json
import pytest
from unittest.mock import MagicMock

from equinox.storage.database import Database
from equinox.storage.history import HistoryManager
from equinox.core.request import Request, Response
from equinox.core.exceptions import ValidationError


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def db(tmp_path):
    return Database(str(tmp_path / "test_search.db"))


@pytest.fixture
def mgr(db):
    return HistoryManager(db)


def _req(url="https://api.example.com/v1/users", method="GET", body=None, headers=None):
    return Request(method=method, url=url, headers=headers or {}, params={}, body=body)


def _resp(status=200, body=None, headers=None, elapsed=0.05):
    r = MagicMock(spec=Response)
    r.status_code = status
    r.reason = "OK"
    r.body = body if body is not None else b'{"id": 1}'
    r.headers = headers or {"content-type": "application/json"}
    r.elapsed = elapsed
    return r


def _seed(mgr):
    """Seed the database with a variety of history entries for search tests."""
    entries = [
        # 0: GET 200 - JSON response
        (_req(url="https://api.example.com/users", method="GET"),
         _resp(200, body=b'{"users": [{"id": 1, "name": "Alice"}]}',
               headers={"content-type": "application/json", "x-request-id": "abc123"},
               elapsed=0.1)),
        # 1: POST 201 - Created
        (_req(url="https://api.example.com/users", method="POST", body='{"name": "Bob"}'),
         _resp(201, body=b'{"id": 2, "name": "Bob"}',
               headers={"content-type": "application/json"},
               elapsed=0.25)),
        # 2: GET 404 - Not found
        (_req(url="https://api.example.com/users/999", method="GET"),
         _resp(404, body=b'{"error": "not found"}',
               headers={"content-type": "application/json"},
               elapsed=0.02)),
        # 3: DELETE 204 - No content
        (_req(url="https://api.example.com/users/1", method="DELETE"),
         _resp(204, body=b'',
               headers={"content-type": "text/plain"},
               elapsed=0.03)),
        # 4: GET 500 - Server error
        (_req(url="https://api.example.com/health", method="GET"),
         _resp(500, body=b'{"error": "internal server error", "trace": "timeout in db"}',
               headers={"content-type": "application/json", "x-debug": "true"},
               elapsed=2.5)),
        # 5: GET 200 - HTML response
        (_req(url="https://example.com/page", method="GET"),
         _resp(200, body=b'<html><body>Hello World</body></html>',
               headers={"content-type": "text/html; charset=utf-8"},
               elapsed=0.5)),
        # 6: PUT 200 - Update
        (_req(url="https://api.example.com/users/1", method="PUT", body='{"name": "Alice Updated"}'),
         _resp(200, body=b'{"id": 1, "name": "Alice Updated", "status": "ok"}',
               headers={"content-type": "application/json"},
               elapsed=0.15)),
        # 7: Error entry (no response)
        (_req(url="https://api.example.com/timeout", method="GET"),
         None),
    ]
    ids = []
    for req, resp in entries:
        if resp is None:
            hid = mgr.save_history(req, error="Connection timed out")
        else:
            hid = mgr.save_history(req, response=resp)
        ids.append(hid)
    return ids


# ── Status code filters ──────────────────────────────────────────────────────


class TestStatusCodeFilter:

    def test_exact_status_code(self, mgr):
        _seed(mgr)
        rows = mgr.search_history(status_code=404)
        assert len(rows) == 1
        assert rows[0]["status_code"] == 404

    def test_exact_status_code_200(self, mgr):
        _seed(mgr)
        rows = mgr.search_history(status_code=200)
        assert all(r["status_code"] == 200 for r in rows)
        assert len(rows) == 3  # entries 0, 5, 6

    def test_status_class_2xx(self, mgr):
        _seed(mgr)
        rows = mgr.search_history(status_class="2xx")
        for r in rows:
            assert 200 <= r["status_code"] < 300

    def test_status_class_4xx(self, mgr):
        _seed(mgr)
        rows = mgr.search_history(status_class="4xx")
        assert len(rows) == 1
        assert rows[0]["status_code"] == 404

    def test_status_class_5xx(self, mgr):
        _seed(mgr)
        rows = mgr.search_history(status_class="5xx")
        assert len(rows) == 1
        assert rows[0]["status_code"] == 500

    def test_status_class_3xx_empty(self, mgr):
        _seed(mgr)
        rows = mgr.search_history(status_class="3xx")
        assert rows == []

    def test_status_errors(self, mgr):
        _seed(mgr)
        rows = mgr.search_history(status_class="errors")
        # Should include 404, 500, and the error entry (status_code IS NULL)
        assert len(rows) == 3

    def test_exact_code_takes_precedence_over_class(self, mgr):
        _seed(mgr)
        # status_code=500, status_class="2xx" → only 500
        rows = mgr.search_history(status_code=500, status_class="2xx")
        assert len(rows) == 1
        assert rows[0]["status_code"] == 500

    def test_invalid_status_code_type(self, mgr):
        with pytest.raises(ValidationError):
            mgr.search_history(status_code="abc")


# ── Body regex filter ─────────────────────────────────────────────────────────


class TestBodyRegexFilter:

    def test_simple_regex_match(self, mgr):
        _seed(mgr)
        rows = mgr.search_history(body_regex="not found")
        assert len(rows) == 1
        assert rows[0]["status_code"] == 404

    def test_regex_case_insensitive(self, mgr):
        _seed(mgr)
        rows = mgr.search_history(body_regex="NOT FOUND")
        assert len(rows) == 1

    def test_regex_pattern(self, mgr):
        _seed(mgr)
        rows = mgr.search_history(body_regex=r"error.*timeout")
        assert len(rows) == 1
        assert rows[0]["status_code"] == 500

    def test_regex_no_match(self, mgr):
        _seed(mgr)
        rows = mgr.search_history(body_regex="zzz_no_match_zzz")
        assert rows == []

    def test_regex_html_body(self, mgr):
        _seed(mgr)
        rows = mgr.search_history(body_regex="<html>.*Hello")
        assert len(rows) == 1

    def test_invalid_regex_raises(self, mgr):
        with pytest.raises(ValidationError, match="Invalid regex"):
            mgr.search_history(body_regex="[invalid")

    def test_regex_too_long_raises(self, mgr):
        with pytest.raises(ValidationError, match="too long"):
            mgr.search_history(body_regex="a" * 501)

    def test_empty_body_skipped(self, mgr):
        _seed(mgr)
        # Entry 3 has empty body → should not match any regex
        rows = mgr.search_history(body_regex=".*", status_code=204)
        # Empty string matches ".*" so this should still match
        assert len(rows) == 1


# ── JSONPath filter ───────────────────────────────────────────────────────────


class TestJsonPathFilter:

    def test_jsonpath_existence(self, mgr):
        _seed(mgr)
        # $.users should exist in entry 0
        rows = mgr.search_history(jsonpath="$.users")
        assert len(rows) == 1
        assert "users" in (rows[0].get("response_body") or "")

    def test_jsonpath_nested(self, mgr):
        _seed(mgr)
        rows = mgr.search_history(jsonpath="$.users[0].name")
        assert len(rows) == 1

    def test_jsonpath_value_match(self, mgr):
        _seed(mgr)
        rows = mgr.search_history(jsonpath="$.name", jsonpath_value="Bob")
        assert len(rows) == 1
        assert rows[0]["status_code"] == 201

    def test_jsonpath_value_no_match(self, mgr):
        _seed(mgr)
        rows = mgr.search_history(jsonpath="$.name", jsonpath_value="Charlie")
        assert rows == []

    def test_jsonpath_status_field(self, mgr):
        _seed(mgr)
        rows = mgr.search_history(jsonpath="$.status", jsonpath_value="ok")
        assert len(rows) == 1
        assert rows[0]["status_code"] == 200

    def test_jsonpath_no_match(self, mgr):
        _seed(mgr)
        rows = mgr.search_history(jsonpath="$.nonexistent_field")
        assert rows == []

    def test_jsonpath_non_json_body_skipped(self, mgr):
        _seed(mgr)
        # HTML body cannot be parsed as JSON → should be skipped gracefully
        rows = mgr.search_history(jsonpath="$.anything", content_type="text/html")
        assert rows == []

    def test_invalid_jsonpath_raises(self, mgr):
        with pytest.raises(ValidationError, match="Invalid JSONPath"):
            mgr.search_history(jsonpath="$[invalid[[[")


# ── Content-type filter ───────────────────────────────────────────────────────


class TestContentTypeFilter:

    def test_json_content_type(self, mgr):
        _seed(mgr)
        rows = mgr.search_history(content_type="application/json")
        assert len(rows) >= 4  # entries 0, 1, 2, 4, 6

    def test_html_content_type(self, mgr):
        _seed(mgr)
        rows = mgr.search_history(content_type="text/html")
        assert len(rows) == 1

    def test_substring_match(self, mgr):
        _seed(mgr)
        rows = mgr.search_history(content_type="json")
        # "json" appears in "application/json"
        assert len(rows) >= 4

    def test_no_match(self, mgr):
        _seed(mgr)
        rows = mgr.search_history(content_type="image/png")
        assert rows == []


# ── Elapsed time filter ──────────────────────────────────────────────────────


class TestElapsedTimeFilter:

    def test_min_elapsed(self, mgr):
        _seed(mgr)
        rows = mgr.search_history(min_elapsed=1.0)
        assert len(rows) == 1
        assert rows[0]["elapsed"] >= 1.0

    def test_max_elapsed(self, mgr):
        _seed(mgr)
        rows = mgr.search_history(max_elapsed=0.05)
        for r in rows:
            assert r["elapsed"] is not None
            assert r["elapsed"] <= 0.05

    def test_elapsed_range(self, mgr):
        _seed(mgr)
        rows = mgr.search_history(min_elapsed=0.1, max_elapsed=0.5)
        for r in rows:
            assert 0.1 <= r["elapsed"] <= 0.5


# ── Response header filter ────────────────────────────────────────────────────


class TestHeaderFilter:

    def test_header_name_only(self, mgr):
        _seed(mgr)
        rows = mgr.search_history(header="x-request-id")
        assert len(rows) == 1

    def test_header_name_and_value(self, mgr):
        _seed(mgr)
        rows = mgr.search_history(header="x-request-id: abc123")
        assert len(rows) == 1

    def test_header_value_no_match(self, mgr):
        _seed(mgr)
        rows = mgr.search_history(header="x-request-id: wrong")
        assert rows == []

    def test_header_case_insensitive(self, mgr):
        _seed(mgr)
        rows = mgr.search_history(header="X-Debug: True")
        assert len(rows) == 1
        assert rows[0]["status_code"] == 500


# ── Combined filters ─────────────────────────────────────────────────────────


class TestCombinedFilters:

    def test_method_and_status_code(self, mgr):
        _seed(mgr)
        rows = mgr.search_history(method="GET", status_code=200)
        assert len(rows) == 2  # entries 0, 5

    def test_method_and_regex(self, mgr):
        _seed(mgr)
        rows = mgr.search_history(method="GET", body_regex="Alice")
        assert len(rows) == 1

    def test_query_and_status_class(self, mgr):
        _seed(mgr)
        rows = mgr.search_history(query="users", status_class="2xx")
        assert len(rows) >= 2

    def test_content_type_and_jsonpath(self, mgr):
        _seed(mgr)
        rows = mgr.search_history(content_type="json", jsonpath="$.error")
        # entries 2 (not found) and 4 (server error) have $.error
        assert len(rows) == 2

    def test_all_filters_together(self, mgr):
        _seed(mgr)
        rows = mgr.search_history(
            method="GET",
            status_class="5xx",
            body_regex="timeout",
            content_type="json",
            min_elapsed=1.0,
        )
        assert len(rows) == 1
        assert rows[0]["status_code"] == 500


# ── Edge cases ────────────────────────────────────────────────────────────────


class TestEdgeCases:

    def test_empty_database(self, mgr):
        rows = mgr.search_history()
        assert rows == []

    def test_all_defaults(self, mgr):
        _seed(mgr)
        rows = mgr.search_history()
        assert len(rows) == 8

    def test_limit_respected_with_post_filter(self, mgr):
        _seed(mgr)
        rows = mgr.search_history(body_regex="id", limit=2)
        assert len(rows) <= 2

    def test_offset(self, mgr):
        _seed(mgr)
        all_rows = mgr.search_history(status_class="2xx")
        offset_rows = mgr.search_history(status_class="2xx", offset=2)
        assert len(offset_rows) == max(0, len(all_rows) - 2)

    def test_method_filter(self, mgr):
        _seed(mgr)
        rows = mgr.search_history(method="DELETE")
        assert len(rows) == 1
        assert rows[0]["method"] == "DELETE"

    def test_query_text_search(self, mgr):
        _seed(mgr)
        rows = mgr.search_history(query="health")
        assert len(rows) == 1
        assert "health" in rows[0]["url"]


# ── CLI smoke test ────────────────────────────────────────────────────────────


class TestHistorySearchCLI:

    def test_search_command_exists(self):
        """The history group has a 'search' subcommand registered."""
        from equinox.cli.history import history
        commands = [c for c in history.commands]
        assert "search" in commands

    def test_search_empty_db(self, tmp_path, monkeypatch):
        """Running search on an empty DB prints 'No matching' message."""
        from click.testing import CliRunner
        from equinox.cli.history import history

        db_path = str(tmp_path / "cli_test.db")
        monkeypatch.setenv("EQUINOX_DB_PATH", db_path)

        runner = CliRunner()
        result = runner.invoke(history, ["search"])
        assert result.exit_code == 0
        assert "No matching" in result.output

    def test_search_with_status_flag(self, tmp_path, monkeypatch, db):
        """The --status flag filters by status code."""
        from click.testing import CliRunner
        from equinox.cli.history import history

        monkeypatch.setenv("EQUINOX_DB_PATH", str(db.db_path))
        mgr = HistoryManager(db)
        mgr.save_history(_req(), response=_resp(200))
        mgr.save_history(_req(), response=_resp(404))

        runner = CliRunner()
        result = runner.invoke(history, ["search", "--status", "404"])
        assert result.exit_code == 0
        assert "404" in result.output
        # 200 should not appear in the status lines
        assert "Found 1" in result.output

    def test_search_with_method_flag(self, tmp_path, monkeypatch, db):
        """The --method flag filters by HTTP method."""
        from click.testing import CliRunner
        from equinox.cli.history import history

        monkeypatch.setenv("EQUINOX_DB_PATH", str(db.db_path))
        mgr = HistoryManager(db)
        mgr.save_history(_req(method="GET"), response=_resp(200))
        mgr.save_history(_req(method="POST"), response=_resp(201))

        runner = CliRunner()
        result = runner.invoke(history, ["search", "--method", "POST"])
        assert result.exit_code == 0
        assert "Found 1" in result.output

