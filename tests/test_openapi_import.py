"""Tests for OpenAPI importer"""

import pytest
import json
import tempfile
from pathlib import Path

from equinox.storage import Database, CollectionManager
from equinox.importers import OpenAPIImporter, preview_spec
from equinox.core.exceptions import ValidationError


class TestOpenAPIImporter:
    """Test OpenAPI specification import"""

    @pytest.fixture
    def db(self):
        """Create temporary database"""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            db_path = tmp.name

        db = Database(db_path)
        yield db

        # Cleanup
        Path(db_path).unlink(missing_ok=True)

    @pytest.fixture
    def col_mgr(self, db):
        """Create collection manager"""
        return CollectionManager(db)

    @pytest.fixture
    def importer(self, col_mgr):
        """Create OpenAPI importer"""
        return OpenAPIImporter(col_mgr)

    @pytest.fixture
    def simple_openapi_3(self):
        """Simple OpenAPI 3.0 spec"""
        return {
            "openapi": "3.0.0",
            "info": {
                "title": "Test API",
                "version": "1.0.0",
                "description": "Test API description"
            },
            "servers": [
                {"url": "https://api.example.com"}
            ],
            "paths": {
                "/users": {
                    "get": {
                        "summary": "List users",
                        "description": "Get all users",
                        "parameters": [
                            {
                                "name": "limit",
                                "in": "query",
                                "schema": {"type": "integer"},
                                "example": 10
                            }
                        ]
                    },
                    "post": {
                        "summary": "Create user",
                        "requestBody": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "name": {"type": "string"},
                                            "email": {"type": "string"}
                                        }
                                    },
                                    "example": {
                                        "name": "John Doe",
                                        "email": "john@example.com"
                                    }
                                }
                            }
                        }
                    }
                },
                "/users/{id}": {
                    "get": {
                        "summary": "Get user",
                        "parameters": [
                            {
                                "name": "id",
                                "in": "path",
                                "required": True,
                                "schema": {"type": "string"}
                            }
                        ]
                    }
                }
            }
        }

    @pytest.fixture
    def swagger_2(self):
        """Swagger 2.0 spec"""
        return {
            "swagger": "2.0",
            "info": {
                "title": "Swagger API",
                "version": "1.0.0"
            },
            "host": "api.example.com",
            "basePath": "/v1",
            "schemes": ["https"],
            "paths": {
                "/items": {
                    "get": {
                        "summary": "List items",
                        "parameters": [
                            {
                                "name": "page",
                                "in": "query",
                                "type": "integer",
                                "default": 1
                            }
                        ]
                    }
                }
            }
        }

    def test_import_openapi_3_from_dict(self, importer, simple_openapi_3):
        """Test importing OpenAPI 3.0 from dictionary"""
        collection_id = importer.import_dict(simple_openapi_3)

        assert collection_id > 0

    def test_collection_created_with_correct_metadata(self, col_mgr, importer, simple_openapi_3):
        """Test collection has correct name and description"""
        collection_id = importer.import_dict(simple_openapi_3)

        collection = col_mgr.get_collection(collection_id)
        assert collection["name"] == "Test API"
        assert collection["description"] == "Test API description"

    def test_requests_imported(self, col_mgr, importer, simple_openapi_3):
        """Test requests are created from paths"""
        collection_id = importer.import_dict(simple_openapi_3)

        requests = col_mgr.list_requests(collection_id)
        assert len(requests) == 3  # GET /users, POST /users, GET /users/{id}

    def test_request_methods_correct(self, col_mgr, importer, simple_openapi_3):
        """Test request methods are correct"""
        collection_id = importer.import_dict(simple_openapi_3)

        requests = col_mgr.list_requests(collection_id)
        methods = [r["method"] for r in requests]

        assert "GET" in methods
        assert "POST" in methods

    def test_request_urls_built_correctly(self, col_mgr, importer, simple_openapi_3):
        """Test URLs are built from server + path"""
        collection_id = importer.import_dict(simple_openapi_3)

        requests = col_mgr.list_requests(collection_id)
        urls = [r["url"] for r in requests]

        assert any("https://api.example.com/users" in url for url in urls)

    def test_path_parameters_converted_to_variables(self, col_mgr, importer, simple_openapi_3):
        """Test path parameters converted to {{variable}} syntax"""
        collection_id = importer.import_dict(simple_openapi_3)

        requests = col_mgr.list_requests(collection_id)

        # Find the GET /users/{id} request
        user_id_request = next(r for r in requests if "/users/{" in r["url"])

        # Should have {{id}} placeholder
        assert "{{id}}" in user_id_request["url"]

    def test_query_parameters_extracted(self, col_mgr, importer, simple_openapi_3):
        """Test query parameters are extracted"""
        collection_id = importer.import_dict(simple_openapi_3)

        requests = col_mgr.list_requests(collection_id)

        # Find GET /users request
        list_users = next(r for r in requests if r["method"] == "GET" and r["url"].endswith("/users"))

        request_obj = col_mgr.get_request(list_users["id"])
        assert "limit" in request_obj.params
        assert request_obj.params["limit"] == "10"

    def test_request_body_extracted(self, col_mgr, importer, simple_openapi_3):
        """Test request body is extracted from OpenAPI 3.x"""
        collection_id = importer.import_dict(simple_openapi_3)

        requests = col_mgr.list_requests(collection_id)

        # Find POST /users request
        create_user = next(r for r in requests if r["method"] == "POST")

        request_obj = col_mgr.get_request(create_user["id"])
        assert request_obj.body is not None

        body_data = json.loads(request_obj.body)
        assert body_data["name"] == "John Doe"
        assert body_data["email"] == "john@example.com"

    def test_import_swagger_2(self, importer, swagger_2):
        """Test importing Swagger 2.0 spec"""
        collection_id = importer.import_dict(swagger_2)

        assert collection_id > 0

    def test_swagger_2_base_url(self, col_mgr, importer, swagger_2):
        """Test Swagger 2.0 base URL is constructed correctly"""
        collection_id = importer.import_dict(swagger_2)

        requests = col_mgr.list_requests(collection_id)
        assert len(requests) == 1

        # URL should be scheme://host/basePath/path
        assert requests[0]["url"] == "https://api.example.com/v1/items"

    def test_unsupported_version_raises_error(self, importer):
        """Test unsupported OpenAPI version raises error"""
        spec = {
            "openapi": "4.0.0",  # Future version
            "info": {"title": "Test"},
            "paths": {}
        }

        with pytest.raises(ValidationError, match="Unsupported"):
            importer.import_dict(spec)

    def test_missing_version_raises_error(self, importer):
        """Test spec without version raises error"""
        spec = {
            "info": {"title": "Test"},
            "paths": {}
        }

        with pytest.raises(ValidationError, match="Missing"):
            importer.import_dict(spec)

    def test_import_from_json_file(self, importer, simple_openapi_3):
        """Test importing from JSON file"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as tmp:
            json.dump(simple_openapi_3, tmp)
            tmp_path = Path(tmp.name)

        try:
            collection_id = importer.import_file(tmp_path)
            assert collection_id > 0
        finally:
            tmp_path.unlink()

    def test_import_from_yaml_file(self, importer):
        """Test importing from YAML file"""
        yaml_content = """
openapi: 3.0.0
info:
  title: YAML API
  version: 1.0.0
paths:
  /test:
    get:
      summary: Test endpoint
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as tmp:
            tmp.write(yaml_content)
            tmp_path = Path(tmp.name)

        try:
            collection_id = importer.import_file(tmp_path)
            assert collection_id > 0
        finally:
            tmp_path.unlink()

    def test_invalid_json_raises_error(self, importer):
        """Test invalid JSON file raises error"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as tmp:
            tmp.write("{ this is not valid json or yaml [[[")
            tmp_path = Path(tmp.name)

        try:
            with pytest.raises(ValidationError, match="Invalid JSON"):
                importer.import_file(tmp_path)
        finally:
            tmp_path.unlink()

    def test_preview_spec(self, simple_openapi_3):
        """Test preview spec functionality"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as tmp:
            json.dump(simple_openapi_3, tmp)
            tmp_path = Path(tmp.name)

        try:
            preview = preview_spec(tmp_path)

            assert preview["title"] == "Test API"
            assert preview["version"] == "1.0.0"
            assert preview["openapi_version"] == "3.0.0"
            assert preview["path_count"] == 2
            assert preview["operation_count"] == 3
            assert preview["size_bytes"] > 0
        finally:
            tmp_path.unlink()

    def test_too_many_paths_raises_error(self, importer):
        """Test too many paths raises security error"""
        spec = {
            "openapi": "3.0.0",
            "info": {"title": "Too Many Paths"},
            "paths": {f"/path{i}": {"get": {}} for i in range(600)}  # Over limit
        }

        with pytest.raises(ValidationError, match="Too many paths"):
            importer.import_dict(spec)

    def test_operation_id_used_for_name(self, col_mgr, importer):
        """Test operationId is used for request name"""
        spec = {
            "openapi": "3.0.0",
            "info": {"title": "Test"},
            "paths": {
                "/users": {
                    "get": {
                        "operationId": "listUsers",
                        "summary": "List all users"
                    }
                }
            }
        }

        collection_id = importer.import_dict(spec)
        requests = col_mgr.list_requests(collection_id)

        assert requests[0]["name"] == "listUsers"

    def test_summary_used_for_name_when_no_operation_id(self, col_mgr, importer):
        """Test summary is used when operationId is missing"""
        spec = {
            "openapi": "3.0.0",
            "info": {"title": "Test"},
            "paths": {
                "/users": {
                    "get": {
                        "summary": "List all users"
                    }
                }
            }
        }

        collection_id = importer.import_dict(spec)
        requests = col_mgr.list_requests(collection_id)

        assert requests[0]["name"] == "List all users"
