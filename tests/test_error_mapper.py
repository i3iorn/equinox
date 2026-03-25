import ssl
import httpx

from types import SimpleNamespace

from equinox.core import error_mapper
from equinox.core.exceptions import CertificateError, RequestError


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

