import json, pytest
from equinox.importers.exporters import CurlExporter, PostmanExporter, OpenAPIExporter, InsomniaExporter
from equinox.core.request import Request
from equinox.storage.database import Database
from equinox.storage.collections import CollectionManager
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
class TestCurlExporter:
    def test_simple_get(self):
        req = Request(method='GET', url='https://example.com/api')
        curl = CurlExporter.export_request(req)
        assert 'curl' in curl
        assert 'https://example.com/api' in curl
    def test_post_with_body(self):
        req = Request(method='POST', url='https://example.com/api',
                      headers={'Content-Type': 'application/json'}, body='{\"key\": \"value\"}')
        curl = CurlExporter.export_request(req)
        assert '-X POST' in curl
class TestPostmanExporter:
    def test_export_and_write(self, populated_db, tmp_path):
        db, col_id = populated_db
        data = PostmanExporter.export_collection(db, col_id)
        assert data['info']['name'] == 'Test API'
        assert len(data['item']) == 2
        out = tmp_path / 'export.json'
        PostmanExporter.export_to_file(data, out)
        assert json.loads(out.read_text())['info']['name'] == 'Test API'
class TestOpenAPIExporter:
    def test_export_and_write(self, populated_db, tmp_path):
        db, col_id = populated_db
        data = OpenAPIExporter.export_collection(db, col_id, title='My API')
        assert data['openapi'] == '3.0.0'
        out = tmp_path / 'openapi.json'
        OpenAPIExporter.export_to_file(data, out)
        assert json.loads(out.read_text())['openapi'] == '3.0.0'
class TestInsomniaExporter:
    def test_export_and_write(self, populated_db, tmp_path):
        db, col_id = populated_db
        data = InsomniaExporter.export_collection(db, col_id)
        assert '_type' in data
        assert len(data['resources']) >= 2
        out = tmp_path / 'insomnia.json'
        InsomniaExporter.export_to_file(data, out)
        assert json.loads(out.read_text())['_type']
