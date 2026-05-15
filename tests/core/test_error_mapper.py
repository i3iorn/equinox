import ssl
import httpx

from types import SimpleNamespace

from equinox.core.format import error_mapper
from equinox.core.exceptions import CertificateError, RequestError, RequestTimeoutError


class DummyReq:
    def __init__(self, url: str):
        self.url = url


def test_connect_error_maps_to_certificate_when_ssl_in_cause():
    client = SimpleNamespace(timeout=30, proxy=None, MAX_REDIRECTS=10)
    handlers = error_mapper.build_error_handlers(client)

    # build a ConnectError whose __cause__ is an ssl.SSLError
    exc = httpx.ConnectError("connect failed")
    exc.__cause__ = ssl.SSLError("certificate verify failed")

    # find the handler for ConnectError
    for exc_type, handler in handlers:
        if exc_type is httpx.ConnectError:
            result = handler(exc, DummyReq("https://example.com"))
            break
    else:
        assert False, "ConnectError handler not found"

    assert isinstance(result["error"], CertificateError)


def test_connect_error_maps_to_generic_request_when_no_ssl():
    client = SimpleNamespace(timeout=30, proxy=None, MAX_REDIRECTS=10)
    handlers = error_mapper.build_error_handlers(client)

    exc = httpx.ConnectError("connect failed")
    # no ssl cause

    for exc_type, handler in handlers:
        if exc_type is httpx.ConnectError:
            result = handler(exc, DummyReq("https://example.com"))
            break
    else:
        assert False, "ConnectError handler not found"

    assert isinstance(result["error"], RequestError)


def test_is_ssl_error_detects_context_chain():
    exc = RuntimeError("outer")
    exc.__context__ = ssl.SSLError("certificate failed")

    assert error_mapper._is_ssl_error(exc) is True


def test_is_proxy_error_detects_http_proxy_traceback():
    compiled = compile("raise RuntimeError('boom')", r"C:\\temp\\http_proxy.py", "exec")
    try:
        exec(compiled, {})
    except RuntimeError as exc:
        assert error_mapper._is_proxy_error(exc) is True
    else:  # pragma: no cover - defensive
        assert False, "RuntimeError was not raised"


def test_suffix_only_includes_text_when_available():
    assert error_mapper._suffix(Exception("")) == ""
    assert error_mapper._suffix(Exception("boom")) == ": boom"


def test_timeout_handler_factory_sets_timeout_details():
    handler = error_mapper._timeout_handler_factory(12.5)
    result = handler(Exception("timeout"), SimpleNamespace(url="https://example.com/api"))

    assert isinstance(result["error"], RequestTimeoutError)
    assert result["error"].details["timeout"] == 12.5
    assert result["audit_tag"] == "timeout"
    assert "12.5s" in result["log_message"]


def test_http_status_and_http_error_handlers():
    status_exc = httpx.HTTPStatusError("err", request=httpx.Request("GET", "https://example.com/api"), response=SimpleNamespace(status_code=418))
    status_result = error_mapper._http_status_handler(
        status_exc,
        SimpleNamespace(url="https://example.com/api"),
    )
    error_result = error_mapper._http_error_handler(
        Exception("transport broken"),
        SimpleNamespace(url="https://example.com/api"),
    )

    assert status_result["error"].details["status"] == 418
    assert "418" in status_result["log_message"]
    assert isinstance(error_result["error"], RequestError)


def test_unicode_encode_handler_returns_request_error():
    result = error_mapper._unicode_encode_handler(
        UnicodeEncodeError("utf-8", "x", 0, 1, "invalid"),
        SimpleNamespace(url="https://example.com/api"),
    )

    assert isinstance(result["error"], RequestError)
    assert "invalid characters" in str(result["error"]).lower()

