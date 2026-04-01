"""Tests for importers/insomnia.py — Insomnia v4 collection importer."""

import json
import pytest
from pathlib import Path
from unittest.mock import Mock, call

from equinox.importers.insomnia import InsomniaImporter


@pytest.fixture
def mock_manager():
    mgr = Mock()
    mgr.create_collection.return_value = 1
    mgr.save_request.return_value = 1
    return mgr


@pytest.fixture
def importer(mock_manager):
    return InsomniaImporter(mock_manager)


def _write_export(tmp_path, data):
    """Helper: write dict as JSON to a temp file and return the Path."""
    p = tmp_path / "export.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


# ── _import_data basics ───────────────────────────────────────────────────


class TestImportDataBasics:
    def test_empty_resources_raises(self, importer):
        with pytest.raises(ValueError, match="No resources"):
            importer._import_data({"resources": []})

    def test_missing_resources_key_raises(self, importer):
        with pytest.raises(ValueError, match="No resources"):
            importer._import_data({})

    def test_single_request(self, importer, mock_manager):
        data = {
            "resources": [
                {"_type": "workspace", "_id": "wrk_1", "name": "My API"},
                {
                    "_type": "request",
                    "_id": "req_1",
                    "parentId": "wrk_1",
                    "name": "Get Users",
                    "method": "GET",
                    "url": "https://api.example.com/users",
                },
            ]
        }
        importer._import_data(data)

        mock_manager.create_collection.assert_called_once_with("My API")
        assert mock_manager.save_request.call_count == 1
        saved = mock_manager.save_request.call_args
        req = saved[0][0]
        assert req.method == "GET"
        assert req.url == "https://api.example.com/users"
        assert req.name == "Get Users"

    def test_no_workspace_uses_default_name(self, importer, mock_manager):
        data = {
            "resources": [
                {
                    "_type": "request",
                    "_id": "req_1",
                    "parentId": "",
                    "name": "R1",
                    "method": "POST",
                    "url": "https://example.com",
                },
            ]
        }
        importer._import_data(data)
        mock_manager.create_collection.assert_called_once_with("Insomnia Import")


class TestImportDataFolders:
    def test_request_in_folder(self, importer, mock_manager):
        data = {
            "resources": [
                {"_type": "workspace", "_id": "wrk_1", "name": "API"},
                {"_type": "request_group", "_id": "fld_1", "parentId": "wrk_1", "name": "Auth"},
                {
                    "_type": "request",
                    "_id": "req_1",
                    "parentId": "fld_1",
                    "name": "Login",
                    "method": "POST",
                    "url": "https://api.example.com/login",
                },
            ]
        }
        importer._import_data(data)
        req = mock_manager.save_request.call_args[0][0]
        assert req.folder == "Auth"

    def test_nested_folders(self, importer, mock_manager):
        data = {
            "resources": [
                {"_type": "workspace", "_id": "wrk_1", "name": "API"},
                {"_type": "request_group", "_id": "fld_1", "parentId": "wrk_1", "name": "Auth"},
                {"_type": "request_group", "_id": "fld_2", "parentId": "fld_1", "name": "OAuth"},
                {
                    "_type": "request",
                    "_id": "req_1",
                    "parentId": "fld_2",
                    "name": "Token",
                    "method": "POST",
                    "url": "https://api.example.com/oauth/token",
                },
            ]
        }
        importer._import_data(data)
        req = mock_manager.save_request.call_args[0][0]
        assert req.folder == "Auth/OAuth"

    def test_request_at_workspace_root(self, importer, mock_manager):
        data = {
            "resources": [
                {"_type": "workspace", "_id": "wrk_1", "name": "API"},
                {
                    "_type": "request",
                    "_id": "req_1",
                    "parentId": "wrk_1",
                    "name": "Health",
                    "method": "GET",
                    "url": "https://api.example.com/health",
                },
            ]
        }
        importer._import_data(data)
        req = mock_manager.save_request.call_args[0][0]
        assert req.folder is None


# ── _import_request: headers, params, body ─────────────────────────────


class TestImportRequestDetails:
    def test_headers_parsed(self, importer, mock_manager):
        data = {
            "resources": [
                {"_type": "workspace", "_id": "wrk_1", "name": "API"},
                {
                    "_type": "request",
                    "_id": "req_1",
                    "parentId": "wrk_1",
                    "name": "R1",
                    "method": "GET",
                    "url": "https://example.com",
                    "headers": [
                        {"name": "Accept", "value": "application/json"},
                        {"name": "X-Disabled", "value": "yes", "disabled": True},
                        {"name": "", "value": "empty-name"},
                    ],
                },
            ]
        }
        importer._import_data(data)
        req = mock_manager.save_request.call_args[0][0]
        assert req.headers == {"accept": "application/json"}

    def test_params_parsed(self, importer, mock_manager):
        data = {
            "resources": [
                {"_type": "workspace", "_id": "wrk_1", "name": "API"},
                {
                    "_type": "request",
                    "_id": "req_1",
                    "parentId": "wrk_1",
                    "name": "R1",
                    "method": "GET",
                    "url": "https://example.com",
                    "parameters": [
                        {"name": "page", "value": "1"},
                        {"name": "disabled_param", "value": "x", "disabled": True},
                    ],
                },
            ]
        }
        importer._import_data(data)
        req = mock_manager.save_request.call_args[0][0]
        assert req.params == {"page": "1"}

    def test_text_body(self, importer, mock_manager):
        data = {
            "resources": [
                {"_type": "workspace", "_id": "wrk_1", "name": "API"},
                {
                    "_type": "request",
                    "_id": "req_1",
                    "parentId": "wrk_1",
                    "name": "Create",
                    "method": "POST",
                    "url": "https://example.com",
                    "body": {"text": '{"name":"test"}'},
                },
            ]
        }
        importer._import_data(data)
        req = mock_manager.save_request.call_args[0][0]
        assert req.body == '{"name":"test"}'

    def test_form_urlencoded_body(self, importer, mock_manager):
        data = {
            "resources": [
                {"_type": "workspace", "_id": "wrk_1", "name": "API"},
                {
                    "_type": "request",
                    "_id": "req_1",
                    "parentId": "wrk_1",
                    "name": "Form",
                    "method": "POST",
                    "url": "https://example.com",
                    "body": {
                        "params": [
                            {"name": "user", "value": "alice"},
                            {"name": "pass", "value": "secret"},
                            {"name": "off", "value": "x", "disabled": True},
                        ]
                    },
                },
            ]
        }
        importer._import_data(data)
        req = mock_manager.save_request.call_args[0][0]
        assert req.body == "user=alice&pass=secret"
        assert req.headers.get("Content-Type") == "application/x-www-form-urlencoded"

    def test_form_urlencoded_no_overwrite_content_type(self, importer, mock_manager):
        data = {
            "resources": [
                {"_type": "workspace", "_id": "wrk_1", "name": "API"},
                {
                    "_type": "request",
                    "_id": "req_1",
                    "parentId": "wrk_1",
                    "name": "Form",
                    "method": "POST",
                    "url": "https://example.com",
                    "headers": [{"name": "Content-Type", "value": "multipart/form-data"}],
                    "body": {
                        "params": [{"name": "k", "value": "v"}]
                    },
                },
            ]
        }
        importer._import_data(data)
        req = mock_manager.save_request.call_args[0][0]
        assert req.headers["Content-Type"] == "multipart/form-data"

    def test_graphql_body(self, importer, mock_manager):
        data = {
            "resources": [
                {"_type": "workspace", "_id": "wrk_1", "name": "API"},
                {
                    "_type": "request",
                    "_id": "req_1",
                    "parentId": "wrk_1",
                    "name": "GQL",
                    "method": "POST",
                    "url": "https://example.com/graphql",
                    "body": {
                        "mimeType": "application/graphql",
                        "text": "{ users { id name } }",
                    },
                },
            ]
        }
        importer._import_data(data)
        req = mock_manager.save_request.call_args[0][0]
        assert req.body == "{ users { id name } }"

    def test_no_body(self, importer, mock_manager):
        data = {
            "resources": [
                {"_type": "workspace", "_id": "wrk_1", "name": "API"},
                {
                    "_type": "request",
                    "_id": "req_1",
                    "parentId": "wrk_1",
                    "name": "Get",
                    "method": "GET",
                    "url": "https://example.com",
                },
            ]
        }
        importer._import_data(data)
        req = mock_manager.save_request.call_args[0][0]
        assert req.body is None

    def test_unnamed_request(self, importer, mock_manager):
        data = {
            "resources": [
                {"_type": "workspace", "_id": "wrk_1", "name": "API"},
                {
                    "_type": "request",
                    "_id": "req_1",
                    "parentId": "wrk_1",
                    "method": "GET",
                    "url": "https://example.com",
                },
            ]
        }
        importer._import_data(data)
        req = mock_manager.save_request.call_args[0][0]
        assert req.name == "Unnamed"

    def test_method_uppercased(self, importer, mock_manager):
        data = {
            "resources": [
                {"_type": "workspace", "_id": "wrk_1", "name": "API"},
                {
                    "_type": "request",
                    "_id": "req_1",
                    "parentId": "wrk_1",
                    "name": "R",
                    "method": "patch",
                    "url": "https://example.com",
                },
            ]
        }
        importer._import_data(data)
        req = mock_manager.save_request.call_args[0][0]
        assert req.method == "PATCH"


# ── import_file: file-level checks ────────────────────────────────────────


class TestImportFile:
    def test_import_file_happy_path(self, importer, mock_manager, tmp_path):
        data = {
            "resources": [
                {"_type": "workspace", "_id": "wrk_1", "name": "File API"},
                {
                    "_type": "request",
                    "_id": "req_1",
                    "parentId": "wrk_1",
                    "name": "Ping",
                    "method": "GET",
                    "url": "https://example.com/ping",
                },
            ]
        }
        path = _write_export(tmp_path, data)
        importer.import_file(path)

        mock_manager.create_collection.assert_called_once_with("File API")
        assert mock_manager.save_request.call_count == 1

    def test_file_too_large(self, importer, tmp_path):
        path = tmp_path / "big.json"
        # Write just over the limit
        path.write_bytes(b"x" * (InsomniaImporter.MAX_FILE_SIZE + 1))
        with pytest.raises(ValueError, match="too large"):
            importer.import_file(path)

    def test_file_not_found(self, importer, tmp_path):
        path = tmp_path / "nonexistent.json"
        with pytest.raises(OSError):
            importer.import_file(path)


# ── Too many requests guard ───────────────────────────────────────────────


class TestTooManyRequests:
    def test_too_many_requests(self, importer):
        resources = [{"_type": "workspace", "_id": "wrk_1", "name": "W"}]
        resources += [
            {
                "_type": "request",
                "_id": f"req_{i}",
                "parentId": "wrk_1",
                "name": f"R{i}",
                "method": "GET",
                "url": "https://example.com",
            }
            for i in range(InsomniaImporter.MAX_REQUESTS + 1)
        ]
        with pytest.raises(ValueError, match="Too many requests"):
            importer._import_data({"resources": resources})


# ── Multiple requests in same import ──────────────────────────────────────


class TestMultipleRequests:
    def test_multiple_requests_imported(self, importer, mock_manager):
        data = {
            "resources": [
                {"_type": "workspace", "_id": "wrk_1", "name": "API"},
                {
                    "_type": "request", "_id": "req_1", "parentId": "wrk_1",
                    "name": "R1", "method": "GET", "url": "https://example.com/1",
                },
                {
                    "_type": "request", "_id": "req_2", "parentId": "wrk_1",
                    "name": "R2", "method": "POST", "url": "https://example.com/2",
                    "body": {"text": "data"},
                },
                {
                    "_type": "request", "_id": "req_3", "parentId": "wrk_1",
                    "name": "R3", "method": "DELETE", "url": "https://example.com/3",
                },
            ]
        }
        importer._import_data(data)
        assert mock_manager.save_request.call_count == 3

