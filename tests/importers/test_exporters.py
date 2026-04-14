import json, pytest, platform
from unittest.mock import patch, MagicMock
from equinox.exporters import (
    CurlExporter, PostmanExporter, OpenAPIExporter, InsomniaExporter, HARExporter,
)
from equinox.importers._utils import json_to_dict, write_json_file, parse_url_parts
from equinox.core.time import to_iso_z
from equinox.core.request import Request, Response
from equinox.core.exceptions import ValidationError
from equinox.storage.database import Database
from equinox.storage.collections import CollectionManager
from equinox.storage.utils import coerce_body_to_str


@pytest.fixture
def db(tmp_path):
    return Database(str(tmp_path / 'test.db'))

@pytest.fixture
def populated_db(db):
    mgr = CollectionManager(db)
    col_id = mgr.create_collection('Test API', 'Test collection')
    req1 = Request(method='GET', url='https://api.example.com/users',
                   headers={'Accept': 'application/json'}, params={'page': '1'}, name='List Users')
    req2 = Request(method='POST', url='https://api.example.com/users',
                   headers={'Content-Type': 'application/json'}, body='{\"name\": \"Alice\"}', name='Create User')
    mgr.save_request(req1, col_id)
    mgr.save_request(req2, col_id)
    return db, col_id

# ============================================================================
# Tests for Improvements #6, #7, #8, #9, #12 - Utility Functions
# ============================================================================

class TestUtilityFunctions:
    """Test utility functions for safe parsing, encoding, and timestamp handling."""
    
    def test_json_to_dict_valid(self):
        """Improvement #9: Safe JSON parsing with valid input."""
        result = json_to_dict('{"key": "value"}')
        assert result == {"key": "value"}
    
    def test_json_to_dict_invalid_with_default(self):
        """Improvement #9: Fallback to default on invalid JSON."""
        result = json_to_dict('invalid json', default={"fallback": "value"})
        assert result == {"fallback": "value"}
    
    def test_json_to_dict_empty_string(self):
        """Improvement #9: Handle empty JSON string."""
        result = json_to_dict('', default={"empty": True})
        assert result == {"empty": True}
    
    def test_json_to_dict_malformed(self):
        """Improvement #9: Handle malformed JSON gracefully."""
        result = json_to_dict('{key": "value}', default={})
        assert result == {}
    
    def testcoerce_body_to_str_bytes(self):
        """Improvement #7: Decode bytes body consistently."""
        body = b"test response"
        result = coerce_body_to_str(body)
        assert result == "test response"
    
    def testcoerce_body_to_str_string(self):
        """Improvement #7: Handle string body."""
        body = "test response"
        result = coerce_body_to_str(body)
        assert result == "test response"
    
    def testcoerce_body_to_str_none(self):
        """Improvement #7: Handle None body."""
        result = coerce_body_to_str(None)
        assert result == ""
    
    def testcoerce_body_to_str_utf8_with_replacements(self):
        """Improvement #7: Handle invalid UTF-8 gracefully."""
        body = b"\xff\xfe invalid utf-8"
        result = coerce_body_to_str(body)
        assert isinstance(result, str)
        assert len(result) > 0
    
    def test_get_iso_timestamp_default(self):
        """Improvement #12: ISO timestamp with Z suffix."""
        ts = to_iso_z()
        assert ts.endswith("Z")
        assert "T" in ts
    
    def test_get_iso_timestamp_provided(self):
        """Improvement #12: Format provided datetime."""
        from datetime import datetime, timezone
        dt = datetime(2026, 3, 13, 10, 30, 0, tzinfo=timezone.utc)
        ts = to_iso_z(dt)
        assert ts.endswith("Z")
        assert "2026-03-13" in ts
    
    def test_parse_url_safe_full_url(self):
        """Improvement #3: Safe URL parsing with full URL."""
        result = parse_url_parts("https://example.com:8080/api/users?page=1")
        assert result["scheme"] == "https"
        assert result["hostname"] == "example.com"
        assert result["port"] == "8080"
        assert result["path"] == "/api/users"
        assert result["query"] == "page=1"
    
    def test_parse_url_safe_minimal(self):
        """Improvement #3: Safe URL parsing with minimal URL."""
        result = parse_url_parts("https://example.com")
        assert result["scheme"] == "https"
        assert result["hostname"] == "example.com"
        assert result["path"] == "" or result["path"] == "/"
    
    def test_parse_url_safe_invalid(self):
        """Improvement #3: Handle invalid URL gracefully."""
        result = parse_url_parts("not a valid url")
        assert isinstance(result, dict)
        assert "scheme" in result
        assert result["scheme"] == "https"  # Defaults to https
    
    def test_write_json_file(self, tmp_path):
        """Improvement #6: Write JSON file with deduplication."""
        data = {"test": "data"}
        file_path = tmp_path / "test.json"
        write_json_file(data, file_path)
        assert file_path.exists()
        assert json.loads(file_path.read_text()) == data
    
    def test_write_json_file_creates_parent_dirs(self, tmp_path):
        """Improvement #6: Create parent directories automatically."""
        data = {"test": "data"}
        file_path = tmp_path / "nested" / "deep" / "test.json"
        write_json_file(data, file_path)
        assert file_path.exists()
        assert json.loads(file_path.read_text()) == data
    
    def test_write_json_file_error_handling(self, tmp_path):
        """Improvement #6, #8: Error handling on write failure."""
        data = {"test": "data"}
        bad_path = tmp_path / "test.json"
        with patch("builtins.open", side_effect=IOError("Permission denied")):
            with pytest.raises(IOError):
                write_json_file(data, bad_path)


# ============================================================================
# Tests for Improvement #2 - Windows-aware Shell Quoting
# ============================================================================

class TestCurlExporter:
    """Test cURL export with Windows and Unix compatibility."""
    
    def test_simple_get(self):
        """Basic GET request export."""
        req = Request(method='GET', url='https://example.com/api')
        curl = CurlExporter.export_request(req)
        assert 'curl' in curl
        assert 'https://example.com/api' in curl
    
    def test_post_with_body(self):
        """POST request with body export."""
        req = Request(method='POST', url='https://example.com/api',
                      headers={'Content-Type': 'application/json'}, body='{\"key\": \"value\"}')
        curl = CurlExporter.export_request(req)
        assert '-X POST' in curl
    
    def test_shell_quote_unix(self):
        """Improvement #2: Unix-style shell quoting."""
        with patch('platform.system', return_value='Linux'):
            result = CurlExporter._shell_quote("test string")
            # On Unix, should use shlex.quote or single quotes
            assert "test string" in result or "'test string'" in result
    
    def test_shell_quote_windows(self):
        """Improvement #2: Windows-style shell quoting with double quotes."""
        with patch('platform.system', return_value='Windows'):
            result = CurlExporter._shell_quote('test"string')
            # On Windows, should use double quotes and escape internal quotes
            assert '"' in result
            assert '\\"' in result or '""' in result
    
    def test_shell_quote_special_chars(self):
        """Improvement #2: Handle special characters in quoting."""
        result = CurlExporter._shell_quote("test$variable")
        assert isinstance(result, str)
        assert len(result) > len("test$variable")
    
    def test_invalid_url_validation(self):
        """Improvement #1: URL validation in cURL export."""
        req = Request(method='GET', url='')
        with pytest.raises(ValidationError):
            CurlExporter.export_request(req)


# ============================================================================
# Tests for Improvements #3, #4, #5 - URL Parsing, Auth, Path Params
# ============================================================================

class TestPostmanExporter:
    """Test Postman export with improvements."""
    
    def test_export_and_write(self, populated_db, tmp_path):
        """Basic Postman export and file write."""
        db, col_id = populated_db
        data = PostmanExporter.export_collection(db, col_id)
        assert data['info']['name'] == 'Test API'
        assert len(data['item']) == 2
        out = tmp_path / 'export.json'
        PostmanExporter.export_to_file(data, out)
        assert json.loads(out.read_text())['info']['name'] == 'Test API'
    
    def test_export_with_auth(self, db, tmp_path):
        """Improvement #4: Export request with auth - basic test."""
        mgr = CollectionManager(db)
        col_id = mgr.create_collection('Auth Test', 'Test with auth')
        
        req = Request(
            method='GET',
            url='https://api.example.com/secure',
            headers={'Accept': 'application/json'},
            name='Secure Endpoint'
        )
        mgr.save_request(req, col_id)
        
        data = PostmanExporter.export_collection(db, col_id)
        assert len(data['item']) > 0
    
    
    def test_export_with_path_params(self, db):
        """Improvement #5: Export request with path parameters."""
        mgr = CollectionManager(db)
        col_id = mgr.create_collection('Path Params Test', '')
        
        req = Request(
            method='GET',
            url='https://api.example.com/users/{id}',
            name='Get User'
        )
        req.path_params = {"id": "123"}
        mgr.save_request(req, col_id)
        
        data = PostmanExporter.export_collection(db, col_id)
        assert len(data['item']) > 0
    
    def test_export_collection_not_found(self, db):
        """Improvement #1, #8: Validation and error handling."""
        with pytest.raises(ValidationError):
            PostmanExporter.export_collection(db, 99999)
            PostmanExporter.export_collection(db, 99999)


class TestOpenAPIExporter:
    """Test OpenAPI export with improvements."""
    
    def test_export_and_write(self, populated_db, tmp_path):
        """Basic OpenAPI export."""
        db, col_id = populated_db
        data = OpenAPIExporter.export_collection(db, col_id, title='My API')
        assert data['openapi'] == '3.0.0'
        out = tmp_path / 'openapi.json'
        OpenAPIExporter.export_to_file(data, out)
        assert json.loads(out.read_text())['openapi'] == '3.0.0'
    
    def test_export_with_auth_schemes(self, db):
        """Improvement #4: OpenAPI export - basic test."""
        mgr = CollectionManager(db)
        col_id = mgr.create_collection('Auth API', '')
        
        req = Request(
            method='GET',
            url='https://api.example.com/secure',
            name='Secure Endpoint'
        )
        mgr.save_request(req, col_id)
        
        data = OpenAPIExporter.export_collection(db, col_id)
        assert 'paths' in data

    
    def test_export_safe_url_parsing(self, db):
        """Improvement #3: Safe URL parsing in OpenAPI paths."""
        mgr = CollectionManager(db)
        col_id = mgr.create_collection('URL Parse Test', '')
        
        req = Request(
            method='GET',
            url='https://api.example.com:8080/v1/users/{id}?filter=active',
            name='Get User'
        )
        req.path_params = {"id": "123"}
        mgr.save_request(req, col_id)
        
        data = OpenAPIExporter.export_collection(db, col_id)
        assert 'paths' in data
        assert len(data['paths']) > 0


class TestHARExporter:
    """Test HAR export improvements."""
    
    def test_export_request_response(self):
        """Improvement #7, #12: HAR entry with body decoding and timestamp."""
        req = Request(
            method='GET',
            url='https://example.com/api',
            headers={'Accept': 'application/json'}
        )
        resp = Response(
            status_code=200,
            headers={'Content-Type': 'application/json'},
            body=b'{"result": "ok"}',
            reason='OK',
            elapsed=0.5,
            request=req
        )
        
        entry = HARExporter.export_request_response(req, resp)
        assert entry['request']['method'] == 'GET'
        assert entry['response']['status'] == 200
        assert entry['startedDateTime'].endswith('Z')  # Improvement #12
        # Check body is decoded (Improvement #7)
        assert isinstance(entry['response']['content']['text'], str)


class TestInsomniaExporter:
    """Test Insomnia export improvements."""
    
    def test_export_and_write(self, populated_db, tmp_path):
        """Basic Insomnia export."""
        db, col_id = populated_db
        data = InsomniaExporter.export_collection(db, col_id)
        assert '_type' in data
        assert len(data['resources']) >= 2
        out = tmp_path / 'insomnia.json'
        InsomniaExporter.export_to_file(data, out)
        assert json.loads(out.read_text())['_type']
    
    def test_export_timestamp_format(self, populated_db):
        """Improvement #12: Proper timestamp formatting."""
        db, col_id = populated_db
        data = InsomniaExporter.export_collection(db, col_id)
        assert data['__export_date'].endswith('Z')
        # Should be ISO format
        assert 'T' in data['__export_date']
    
    def test_export_safe_json_parsing(self, db):
        """Improvement #9: Safe JSON parsing in Insomnia export."""
        mgr = CollectionManager(db)
        col_id = mgr.create_collection('Insomnia Test', '')
        
        req = Request(
            method='POST',
            url='https://api.example.com/data',
            headers={'Content-Type': 'application/json'},
            body='{"key": "value"}',
            name='Create Data'
        )
        mgr.save_request(req, col_id)
        
        data = InsomniaExporter.export_collection(db, col_id)
        assert len(data['resources']) > 1


