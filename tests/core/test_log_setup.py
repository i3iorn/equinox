"""Tests for log_setup module."""
import json, logging
import equinox.core.log_setup as log_setup
class TestLogSetup:
    def test_configure_creates_log_file(self, tmp_path):
        log_dir = tmp_path / 'logs'
        log_file = log_setup.configure_logging(log_dir=log_dir)
        assert log_file.exists() or log_file.parent.exists()
        assert log_dir.exists()
    def test_json_log_format(self, tmp_path):
        log_dir = tmp_path / 'logs'
        log_file = log_setup.configure_logging(log_dir=log_dir)
        logger = logging.getLogger('test_json_fmt')
        logger.info('Test message', extra={'method': 'GET', 'url': 'https://example.com', 'status': 200})
        # Flush
        for h in logging.getLogger().handlers:
            h.flush()
        content = log_file.read_text()
        lines = [l for l in content.strip().split('\n') if l.strip()]
        # Find our test message
        found = False
        for line in lines:
            doc = json.loads(line)
            if doc.get('msg') == 'Test message':
                assert doc['method'] == 'GET'
                assert doc['status'] == 200
                assert 'ts' in doc
                found = True
                break
        assert found, f'Test message not found in log. Lines: {lines}'
    def test_get_log_file_before_configure(self):
        # Clear handlers
        root = logging.getLogger()
        root.handlers.clear()
        assert log_setup.get_log_file() is None
    def test_get_log_file_after_configure(self, tmp_path):
        log_dir = tmp_path / 'logs2'
        log_file = log_setup.configure_logging(log_dir=log_dir)
        result = log_setup.get_log_file()
        assert result is not None
        assert str(result).endswith('equinox.log')
class TestJsonFormatter:
    def test_formats_valid_json(self):
        fmt = log_setup.JsonFormatter()
        record = logging.LogRecord('test', logging.INFO, '', 0, 'hello', (), None)
        output = fmt.format(record)
        doc = json.loads(output)
        assert doc['msg'] == 'hello'
        assert doc['level'] == 'INFO'
        assert 'ts' in doc
        assert 'app_corr_id' in doc
    def test_includes_exception(self):
        fmt = log_setup.JsonFormatter()
        try:
            raise ValueError('boom')
        except ValueError:
            import sys
            record = logging.LogRecord('test', logging.ERROR, '', 0, 'error', (), sys.exc_info())
        output = fmt.format(record)
        doc = json.loads(output)
        assert 'exc' in doc
        assert 'boom' in doc['exc']


def test_reset_app_corr_id_regenerates_value():
    reset_fn = getattr(log_setup, "reset_app_corr_id")
    first = reset_fn()
    second = reset_fn()

    assert first != second
    assert log_setup.get_app_corr_id() == second


def test_json_formatter_uses_context_request_id():
    fmt = log_setup.JsonFormatter()
    token = log_setup.set_current_request_id("ctx123456789")
    try:
        record = logging.LogRecord("test", logging.INFO, "", 0, "hello", (), None)
        output = fmt.format(record)
    finally:
        log_setup.clear_current_request_id(token)
    doc = json.loads(output)
    assert doc["request_id"] == "ctx123456789"


