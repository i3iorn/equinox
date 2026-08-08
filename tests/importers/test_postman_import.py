"""Tests for Postman collection importer"""

import json
import tempfile
from pathlib import Path

import pytest

from equinox.core.exceptions import ValidationError
from equinox.importers import PostmanImporter, preview_collection
from equinox.storage import CollectionManager, Database


class TestPostmanImporter:
    """Test Postman collection import"""

    @pytest.fixture
    def db(self):
        """Create temporary database"""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            db_path = tmp.name

        db = Database(db_path)
        yield db

        # Cleanup
        db.close()
        Path(db_path).unlink(missing_ok=True)

    @pytest.fixture
    def col_mgr(self, db):
        """Create collection manager"""
        return CollectionManager(db)

    @pytest.fixture
    def importer(self, col_mgr):
        """Create Postman importer"""
        return PostmanImporter(col_mgr)

    @pytest.fixture
    def simple_postman_v21(self):
        """Simple Postman v2.1 collection"""
        return {
            "info": {
                "name": "Test Collection",
                "description": "Test description",
                "_postman_id": "12345",
                "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
            },
            "item": [
                {
                    "name": "Get Users",
                    "request": {
                        "method": "GET",
                        "header": [],
                        "url": {
                            "raw": "https://api.example.com/users",
                            "protocol": "https",
                            "host": ["api", "example", "com"],
                            "path": ["users"],
                        },
                    },
                },
                {
                    "name": "Create User",
                    "request": {
                        "method": "POST",
                        "header": [{"key": "Content-Type", "value": "application/json"}],
                        "body": {
                            "mode": "raw",
                            "raw": '{"name": "John Doe", "email": "john@example.com"}',
                        },
                        "url": {
                            "raw": "https://api.example.com/users",
                            "protocol": "https",
                            "host": ["api", "example", "com"],
                            "path": ["users"],
                        },
                    },
                },
            ],
        }

    @pytest.fixture
    def simple_postman_v20(self):
        """Simple Postman v2.0 collection"""
        return {
            "info": {
                "name": "V2.0 Collection",
                "description": "Version 2.0 test",
                "schema": "https://schema.getpostman.com/json/collection/v2.0.0/collection.json",
            },
            "item": [
                {
                    "name": "Simple Request",
                    "request": {"method": "GET", "url": "https://api.example.com/test"},
                },
            ],
        }

    def test_import_v21_from_dict(self, importer, simple_postman_v21):
        """Test importing Postman v2.1 from dictionary"""
        collection_id = importer.import_dict(simple_postman_v21)
        assert collection_id > 0

    def test_collection_created_with_correct_metadata(self, col_mgr, importer, simple_postman_v21):
        """Test collection has correct name and description"""
        collection_id = importer.import_dict(simple_postman_v21)

        collection = col_mgr.get_collection(collection_id)
        assert collection["name"] == "Test Collection"
        assert collection["description"] == "Test description"

    def test_requests_imported(self, col_mgr, importer, simple_postman_v21):
        """Test requests are created from items"""
        collection_id = importer.import_dict(simple_postman_v21)

        requests = col_mgr.list_requests(collection_id)
        assert len(requests) == 2

    def test_request_names_correct(self, col_mgr, importer, simple_postman_v21):
        """Test request names are correct"""
        collection_id = importer.import_dict(simple_postman_v21)

        requests = col_mgr.list_requests(collection_id)
        names = [r["name"] for r in requests]

        assert "Get Users" in names
        assert "Create User" in names

    def test_request_methods_correct(self, col_mgr, importer, simple_postman_v21):
        """Test request methods are correct"""
        collection_id = importer.import_dict(simple_postman_v21)

        requests = col_mgr.list_requests(collection_id)
        methods = [r["method"] for r in requests]

        assert "GET" in methods
        assert "POST" in methods

    def test_request_urls_parsed_correctly(self, col_mgr, importer, simple_postman_v21):
        """Test URLs are parsed correctly"""
        collection_id = importer.import_dict(simple_postman_v21)

        requests = col_mgr.list_requests(collection_id)
        urls = [r["url"] for r in requests]

        assert any("https://api.example.com/users" in url for url in urls)

    def test_headers_extracted(self, col_mgr, importer, simple_postman_v21):
        """Test headers are extracted"""
        collection_id = importer.import_dict(simple_postman_v21)

        requests = col_mgr.list_requests(collection_id)

        # Find POST request
        create_user = next(r for r in requests if r["method"] == "POST")

        request_obj = col_mgr.get_request(create_user["id"])
        assert "Content-Type" in request_obj.headers
        assert request_obj.headers["Content-Type"] == "application/json"

    def test_request_body_extracted(self, col_mgr, importer, simple_postman_v21):
        """Test request body is extracted"""
        collection_id = importer.import_dict(simple_postman_v21)

        requests = col_mgr.list_requests(collection_id)

        # Find POST request
        create_user = next(r for r in requests if r["method"] == "POST")

        request_obj = col_mgr.get_request(create_user["id"])
        assert request_obj.body is not None

        body_data = json.loads(request_obj.body)
        assert body_data["name"] == "John Doe"
        assert body_data["email"] == "john@example.com"

    def test_import_v20(self, importer, simple_postman_v20):
        """Test importing Postman v2.0 collection"""
        collection_id = importer.import_dict(simple_postman_v20)
        assert collection_id > 0

    def test_v20_with_string_url(self, col_mgr, importer, simple_postman_v20):
        """Test v2.0 with string URL"""
        collection_id = importer.import_dict(simple_postman_v20)

        requests = col_mgr.list_requests(collection_id)
        assert len(requests) == 1
        assert requests[0]["url"] == "https://api.example.com/test"

    def test_unsupported_version_raises_error(self, importer):
        """Test unsupported Postman version raises error"""
        collection = {
            "info": {
                "name": "Test",
                "schema": "https://schema.getpostman.com/json/collection/v3.0.0/collection.json",
            },
            "item": [],
        }

        with pytest.raises(ValidationError, match="Unsupported"):
            importer.import_dict(collection)

    def test_missing_info_raises_error(self, importer):
        """Test collection without info section raises error"""
        collection = {"item": []}

        with pytest.raises(ValidationError, match="missing 'info' field"):
            importer.import_dict(collection)

    def test_import_from_json_file(self, importer, simple_postman_v21):
        """Test importing from JSON file"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tmp:
            json.dump(simple_postman_v21, tmp)
            tmp_path = Path(tmp.name)

        try:
            collection_id = importer.import_file(tmp_path)
            assert collection_id > 0
        finally:
            tmp_path.unlink()

    def test_invalid_json_raises_error(self, importer):
        """Test invalid JSON file raises error"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tmp:
            tmp.write("{ invalid json [[[")
            tmp_path = Path(tmp.name)

        try:
            with pytest.raises(ValidationError, match="Invalid JSON"):
                importer.import_file(tmp_path)
        finally:
            tmp_path.unlink()

    def test_file_not_found_raises_error(self, importer):
        """Test non-existent file raises error"""
        with pytest.raises(ValidationError, match="not found"):
            importer.import_file(Path("/nonexistent/file.json"))

    def test_file_too_large_raises_error(self, importer):
        """Test file size limit"""
        # Create a large file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tmp:
            # Write more than MAX_COLLECTION_SIZE
            large_data = {"info": {"name": "Test"}, "item": [], "data": "x" * (11 * 1024 * 1024)}
            json.dump(large_data, tmp)
            tmp_path = Path(tmp.name)

        try:
            with pytest.raises(ValidationError, match="too large"):
                importer.import_file(tmp_path)
        finally:
            tmp_path.unlink()

    def test_too_many_requests_raises_error(self, importer):
        """Test too many requests raises error"""
        collection = {
            "info": {
                "name": "Too Many",
                "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
            },
            "item": [
                {"name": f"Request {i}", "request": {"method": "GET", "url": "https://example.com"}}
                for i in range(1001)  # Over MAX_REQUESTS
            ],
        }

        with pytest.raises(ValidationError, match="Too many requests"):
            importer.import_dict(collection)

    def test_nested_folders(self, col_mgr, importer):
        """Test importing nested folders"""
        collection = {
            "info": {
                "name": "Nested Collection",
                "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
            },
            "item": [
                {
                    "name": "Folder 1",
                    "item": [
                        {
                            "name": "Request 1",
                            "request": {"method": "GET", "url": "https://api.example.com/1"},
                        },
                        {
                            "name": "Subfolder",
                            "item": [
                                {
                                    "name": "Request 2",
                                    "request": {
                                        "method": "GET",
                                        "url": "https://api.example.com/2",
                                    },
                                },
                            ],
                        },
                    ],
                },
            ],
        }

        collection_id = importer.import_dict(collection)
        requests = col_mgr.list_requests(collection_id)

        # Should flatten nested items
        assert len(requests) == 2

    def test_query_parameters(self, col_mgr, importer):
        """Test query parameters are stored in params_list, not baked into the URL."""
        collection = {
            "info": {
                "name": "Query Test",
                "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
            },
            "item": [
                {
                    "name": "With Query",
                    "request": {
                        "method": "GET",
                        "url": {
                            "raw": "https://api.example.com/search?q=test&limit=10",
                            "query": [
                                {"key": "q", "value": "test"},
                                {"key": "limit", "value": "10"},
                            ],
                        },
                    },
                },
            ],
        }

        collection_id = importer.import_dict(collection)
        requests = col_mgr.list_requests(collection_id)

        # Params are now stored in params / params_list, NOT embedded in the URL
        request_obj = col_mgr.get_request(requests[0]["id"])
        assert request_obj.params.get("q") == "test"
        assert request_obj.params.get("limit") == "10"
        assert "?" not in request_obj.url
        pl = request_obj.params_list or []
        assert any(p["key"] == "q" and p["enabled"] for p in pl)
        assert any(p["key"] == "limit" and p["enabled"] for p in pl)

    def test_preview_collection(self, simple_postman_v21):
        """Test preview collection functionality"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tmp:
            json.dump(simple_postman_v21, tmp)
            tmp_path = Path(tmp.name)

        try:
            preview = preview_collection(tmp_path)

            assert preview["name"] == "Test Collection"
            assert preview["description"] == "Test description"
            assert preview["request_count"] == 2
            assert preview["size_bytes"] > 0
        finally:
            tmp_path.unlink()

    def test_request_without_name_uses_default(self, col_mgr, importer):
        """Test request without name gets default name"""
        collection = {
            "info": {
                "name": "Test",
                "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
            },
            "item": [{"request": {"method": "GET", "url": "https://api.example.com/test"}}],
        }

        collection_id = importer.import_dict(collection)
        requests = col_mgr.list_requests(collection_id)

        assert len(requests) == 1
        assert requests[0]["name"] == "Untitled Request"

    def test_empty_collection(self, col_mgr, importer):
        """Test importing empty collection"""
        collection = {
            "info": {
                "name": "Empty",
                "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
            },
            "item": [],
        }

        collection_id = importer.import_dict(collection)
        requests = col_mgr.list_requests(collection_id)

        assert len(requests) == 0

    def test_url_with_variables(self, col_mgr, importer):
        """Test URL with embedded variables is preserved"""
        collection = {
            "info": {
                "name": "Variables",
                "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
            },
            "item": [
                {
                    "name": "Variable Test",
                    "request": {"method": "GET", "url": "https://{{host}}/users/{{userId}}"},
                },
            ],
        }

        collection_id = importer.import_dict(collection)
        requests = col_mgr.list_requests(collection_id)

        request_obj = col_mgr.get_request(requests[0]["id"])
        # Variables should be preserved in URL
        assert "{{host}}" in request_obj.url
        assert "{{userId}}" in request_obj.url

    def test_different_body_modes(self, col_mgr, importer):
        """Test different body modes"""
        collection = {
            "info": {
                "name": "Body Modes",
                "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
            },
            "item": [
                {
                    "name": "Raw JSON",
                    "request": {
                        "method": "POST",
                        "url": "https://api.example.com",
                        "body": {"mode": "raw", "raw": '{"key": "value"}'},
                    },
                },
                {
                    "name": "URLEncoded",
                    "request": {
                        "method": "POST",
                        "url": "https://api.example.com",
                        "body": {
                            "mode": "urlencoded",
                            "urlencoded": [
                                {"key": "field1", "value": "value1"},
                                {"key": "field2", "value": "value2"},
                            ],
                        },
                    },
                },
            ],
        }

        collection_id = importer.import_dict(collection)
        requests = col_mgr.list_requests(collection_id)

        assert len(requests) == 2

        # Check raw body
        raw_req = col_mgr.get_request(requests[0]["id"])
        assert raw_req.body == '{"key": "value"}'

        # Check urlencoded body - should be converted to form params
        form_req = col_mgr.get_request(requests[1]["id"])
        assert form_req.body == "field1=value1&field2=value2"

    def test_raw_url_colon_path_variable_normalized(self, col_mgr, importer):
        collection = {
            "info": {
                "name": "Path Vars",
                "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
            },
            "item": [
                {
                    "name": "Get User",
                    "request": {
                        "method": "GET",
                        "url": "https://api.example.com/users/:userId",
                    },
                },
            ],
        }

        collection_id = importer.import_dict(collection)
        requests = col_mgr.list_requests(collection_id)
        request_obj = col_mgr.get_request(requests[0]["id"])
        assert request_obj.url == "https://api.example.com/users/{{userId}}"

    def test_structured_url_single_brace_variable_normalized(self, col_mgr, importer):
        collection = {
            "info": {
                "name": "Path Vars Structured",
                "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
            },
            "item": [
                {
                    "name": "Get User",
                    "request": {
                        "method": "GET",
                        "url": {
                            "protocol": "https",
                            "host": ["api", "example", "com"],
                            "path": ["users", "{userId}"],
                        },
                    },
                },
            ],
        }

        collection_id = importer.import_dict(collection)
        requests = col_mgr.list_requests(collection_id)
        request_obj = col_mgr.get_request(requests[0]["id"])
        assert request_obj.url == "https://api.example.com/users/{{userId}}"
