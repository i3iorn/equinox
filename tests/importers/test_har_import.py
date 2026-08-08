"""Tests for HARImporter."""

import json
from pathlib import Path

import pytest

from equinox.importers.har import HARImporter
from equinox.storage.collections import CollectionManager
from equinox.storage.database import Database

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_db(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    yield db
    db.close()


@pytest.fixture
def mgr(tmp_db):
    return CollectionManager(tmp_db)


def _write_har(tmp_path: Path, har: dict, name: str = "test.har") -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(har), encoding="utf-8")
    return p


def _minimal_har(entries=None, title="My API") -> dict:
    return {
        "log": {
            "version": "1.2",
            "title": title,
            "creator": {"name": "test", "version": "1"},
            "entries": entries or [],
        },
    }


def _get_entry(url="https://example.com", headers=None, qs=None) -> dict:
    return {
        "startedDateTime": "2024-01-01T00:00:00.000Z",
        "time": 100,
        "request": {
            "method": "GET",
            "url": url,
            "headers": headers or [],
            "queryString": qs or [],
            "headersSize": -1,
            "bodySize": -1,
        },
        "response": {
            "status": 200,
            "statusText": "OK",
            "headers": [],
            "content": {"size": 0, "mimeType": "application/json"},
            "redirectURL": "",
            "headersSize": -1,
            "bodySize": -1,
        },
    }


def _post_entry(url="https://example.com/data", body='{"key":"value"}') -> dict:
    return {
        "startedDateTime": "2024-01-01T00:00:00.000Z",
        "time": 100,
        "request": {
            "method": "POST",
            "url": url,
            "headers": [{"name": "Content-Type", "value": "application/json"}],
            "queryString": [],
            "postData": {"mimeType": "application/json", "text": body},
            "headersSize": -1,
            "bodySize": len(body),
        },
        "response": {
            "status": 201,
            "statusText": "Created",
            "headers": [],
            "content": {"size": 0, "mimeType": "application/json"},
            "redirectURL": "",
            "headersSize": -1,
            "bodySize": -1,
        },
    }


# ── Basic import ──────────────────────────────────────────────────────────────


class TestHARImportBasic:
    def test_returns_collection_id(self, mgr, tmp_path):
        p = _write_har(tmp_path, _minimal_har())
        col_id = HARImporter(mgr).import_file(p)
        assert isinstance(col_id, int)
        assert col_id > 0

    def test_collection_named_from_title(self, mgr, tmp_path):
        har = _minimal_har(title="Auth Tests")
        p = _write_har(tmp_path, har)
        col_id = HARImporter(mgr).import_file(p)
        col = mgr.get_collection(col_id)
        assert col["name"] == "Auth Tests"

    def test_collection_named_from_filename_when_no_title(self, mgr, tmp_path):
        har = _minimal_har(title="")
        p = _write_har(tmp_path, har, name="my_requests.har")
        col_id = HARImporter(mgr).import_file(p)
        col = mgr.get_collection(col_id)
        assert "my_requests" in col["name"]

    def test_single_get_entry_imported(self, mgr, tmp_path):
        har = _minimal_har(entries=[_get_entry("https://api.example.com/users")])
        p = _write_har(tmp_path, har)
        col_id = HARImporter(mgr).import_file(p)
        reqs = mgr.list_requests(col_id)
        assert len(reqs) == 1

    def test_request_url_preserved(self, mgr, tmp_path):
        har = _minimal_har(entries=[_get_entry("https://api.example.com/users")])
        p = _write_har(tmp_path, har)
        col_id = HARImporter(mgr).import_file(p)
        reqs = mgr.list_requests(col_id)
        req = mgr.get_request(reqs[0]["id"])
        assert req.url == "https://api.example.com/users"

    def test_request_method_preserved(self, mgr, tmp_path):
        har = _minimal_har(entries=[_get_entry()])
        p = _write_har(tmp_path, har)
        col_id = HARImporter(mgr).import_file(p)
        reqs = mgr.list_requests(col_id)
        req = mgr.get_request(reqs[0]["id"])
        assert req.method == "GET"

    def test_multiple_entries_imported(self, mgr, tmp_path):
        entries = [
            _get_entry("https://api.example.com/users"),
            _get_entry("https://api.example.com/posts"),
            _post_entry("https://api.example.com/users"),
        ]
        har = _minimal_har(entries=entries)
        p = _write_har(tmp_path, har)
        col_id = HARImporter(mgr).import_file(p)
        reqs = mgr.list_requests(col_id)
        assert len(reqs) == 3


# ── Headers and body ──────────────────────────────────────────────────────────


class TestHARHeaders:
    def test_request_headers_preserved(self, mgr, tmp_path):
        headers = [
            {"name": "Authorization", "value": "Bearer token123"},
            {"name": "Accept", "value": "application/json"},
        ]
        har = _minimal_har(entries=[_get_entry(headers=headers)])
        p = _write_har(tmp_path, har)
        col_id = HARImporter(mgr).import_file(p)
        reqs = mgr.list_requests(col_id)
        req = mgr.get_request(reqs[0]["id"])
        assert req.headers.get("Authorization") == "Bearer token123"
        assert req.headers.get("Accept") == "application/json"

    def test_http2_pseudoheaders_skipped(self, mgr, tmp_path):
        headers = [
            {"name": ":method", "value": "GET"},
            {"name": ":path", "value": "/"},
            {"name": "X-Custom", "value": "hello"},
        ]
        har = _minimal_har(entries=[_get_entry(headers=headers)])
        p = _write_har(tmp_path, har)
        col_id = HARImporter(mgr).import_file(p)
        reqs = mgr.list_requests(col_id)
        req = mgr.get_request(reqs[0]["id"])
        assert ":method" not in req.headers
        assert req.headers.get("X-Custom") == "hello"

    def test_post_body_preserved(self, mgr, tmp_path):
        body = '{"username":"alice","password":"secret"}'
        har = _minimal_har(entries=[_post_entry(body=body)])
        p = _write_har(tmp_path, har)
        col_id = HARImporter(mgr).import_file(p)
        reqs = mgr.list_requests(col_id)
        req = mgr.get_request(reqs[0]["id"])
        assert req.method == "POST"
        assert req.body == body

    def test_query_params_preserved(self, mgr, tmp_path):
        qs = [{"name": "page", "value": "1"}, {"name": "limit", "value": "10"}]
        har = _minimal_har(entries=[_get_entry(qs=qs)])
        p = _write_har(tmp_path, har)
        col_id = HARImporter(mgr).import_file(p)
        reqs = mgr.list_requests(col_id)
        req = mgr.get_request(reqs[0]["id"])
        assert req.params.get("page") == "1"
        assert req.params.get("limit") == "10"


# ── Error handling ────────────────────────────────────────────────────────────


class TestHARErrorHandling:
    def test_invalid_json_raises_value_error(self, mgr, tmp_path):
        p = tmp_path / "bad.har"
        p.write_text("this is not json", encoding="utf-8")
        with pytest.raises(ValueError, match="Invalid JSON"):
            HARImporter(mgr).import_file(p)

    def test_missing_log_key_raises(self, mgr, tmp_path):
        p = tmp_path / "nokey.har"
        p.write_text('{"not_log": {}}', encoding="utf-8")
        with pytest.raises(ValueError, match="valid HAR"):
            HARImporter(mgr).import_file(p)

    def test_missing_file_raises(self, mgr, tmp_path):
        p = tmp_path / "nonexistent.har"
        with pytest.raises(ValueError, match="HAR file not found"):
            HARImporter(mgr).import_file(p)

    def test_malformed_entry_skipped_gracefully(self, mgr, tmp_path):
        entries = [
            {"request": None},  # bad entry
            _get_entry("https://valid.example.com"),
        ]
        har = _minimal_har(entries=entries)
        p = _write_har(tmp_path, har)
        col_id = HARImporter(mgr).import_file(p)
        reqs = mgr.list_requests(col_id)
        # Bad entry is skipped; valid entry is imported
        assert len(reqs) == 1

    def test_data_uri_skipped(self, mgr, tmp_path):
        entries = [
            _get_entry("data:image/png;base64,abc123"),
            _get_entry("https://api.example.com"),
        ]
        har = _minimal_har(entries=entries)
        p = _write_har(tmp_path, har)
        col_id = HARImporter(mgr).import_file(p)
        reqs = mgr.list_requests(col_id)
        assert len(reqs) == 1
        req = mgr.get_request(reqs[0]["id"])
        assert "api.example.com" in req.url

    def test_binary_content_type_skipped(self, mgr, tmp_path):
        binary_entry = {
            "startedDateTime": "2024-01-01T00:00:00.000Z",
            "time": 10,
            "request": {
                "method": "POST",
                "url": "https://example.com/upload",
                "headers": [],
                "queryString": [],
                "postData": {"mimeType": "image/png", "text": "binary_data"},
                "headersSize": -1,
                "bodySize": 100,
            },
            "response": {
                "status": 200,
                "statusText": "OK",
                "headers": [],
                "content": {"size": 0, "mimeType": "application/json"},
                "redirectURL": "",
                "headersSize": -1,
                "bodySize": -1,
            },
        }
        entries = [binary_entry, _get_entry("https://api.example.com")]
        har = _minimal_har(entries=entries)
        p = _write_har(tmp_path, har)
        col_id = HARImporter(mgr).import_file(p)
        reqs = mgr.list_requests(col_id)
        assert len(reqs) == 1
        req = mgr.get_request(reqs[0]["id"])
        assert req.url == "https://api.example.com"

    def test_empty_entries_creates_empty_collection(self, mgr, tmp_path):
        har = _minimal_har(entries=[])
        p = _write_har(tmp_path, har)
        col_id = HARImporter(mgr).import_file(p)
        reqs = mgr.list_requests(col_id)
        assert len(reqs) == 0
        assert mgr.get_collection(col_id) is not None
