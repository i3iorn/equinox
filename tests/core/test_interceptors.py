"""Tests for the interceptor system and request/response logger."""

import json
import logging
import pytest
from unittest.mock import MagicMock, patch

from equinox.core.interceptors import (
    InterceptorContext,
    RequestInterceptor,
    ResponseInterceptor,
    ErrorInterceptor,
    InterceptorChain,
    RequestResponseLogger,
    LoggingRequestInterceptor,
    LoggingResponseInterceptor,
    LoggingErrorInterceptor, InterceptorAction, InterceptorResult,
)
from equinox.core.request import Request, Response


# ── Helpers ──────────────────────────────────────────────────────────────────

def _req(method="GET", url="https://example.com/api"):
    return Request(method=method, url=url, headers={}, params={}, body=None)


def _resp(status=200, body=b"ok", headers=None):
    r = MagicMock(spec=Response)
    r.status_code = status
    r.reason = "OK"
    r.body = body
    r.headers = headers or {"content-type": "application/json"}
    r.elapsed = 0.123
    return r


# ── InterceptorContext ────────────────────────────────────────────────────────

class TestInterceptorContext:

    def test_timestamp_set_on_init(self):
        req = _req()
        ctx = InterceptorContext(request=req)
        assert ctx.timestamp is not None

    def test_metadata_defaults_to_empty_dict(self):
        req = _req()
        ctx = InterceptorContext(request=req)
        assert ctx.metadata == {}

    def test_can_set_response_and_error(self):
        req = _req()
        err = ValueError("oops")
        ctx = InterceptorContext(request=req, error=err)
        assert ctx.error is err


# ── Base interceptor defaults ─────────────────────────────────────────────────

class TestBaseInterceptors:

    def test_request_interceptor_can_intercept_returns_true(self):
        req = _req()
        assert RequestInterceptor().can_intercept(req) is True

    def test_request_interceptor_intercept_returns_continue(self):
        req = _req()
        ctx = InterceptorContext(request=req)
        assert RequestInterceptor().intercept(ctx).action == InterceptorAction.CONTINUE

    def test_response_interceptor_can_intercept_returns_true(self):
        resp = _resp()
        assert ResponseInterceptor().can_intercept(resp) is True

    def test_response_interceptor_intercept_returns_none(self):
        req = _req()
        ctx = InterceptorContext(request=req, response=_resp())
        assert RequestInterceptor().intercept(ctx).action == InterceptorAction.CONTINUE

    def test_error_interceptor_can_intercept_returns_true(self):
        req = _req()
        err = RuntimeError("boom")
        assert ErrorInterceptor().can_intercept(err, req) is True

    def test_error_interceptor_intercept_returns_error(self):
        req = _req()
        err = RuntimeError("boom")
        ctx = InterceptorContext(request=req, error=err)
        assert RequestInterceptor().intercept(ctx).action == InterceptorAction.CONTINUE


# ── InterceptorChain ──────────────────────────────────────────────────────────

class TestInterceptorChain:

    def test_add_request_interceptor(self):
        chain = InterceptorChain()
        chain.add_request_interceptor(RequestInterceptor())
        assert len(chain.request_interceptors) == 1

    def test_add_response_interceptor(self):
        chain = InterceptorChain()
        chain.add_response_interceptor(ResponseInterceptor())
        assert len(chain.response_interceptors) == 1

    def test_add_error_interceptor(self):
        chain = InterceptorChain()
        chain.add_error_interceptor(ErrorInterceptor())
        assert len(chain.error_interceptors) == 1

    def test_process_request_no_interceptors(self):
        chain = InterceptorChain()
        req = _req()
        result = chain.process_request(req)
        assert result is req

    def test_process_request_passthrough(self):
        chain = InterceptorChain()
        chain.add_request_interceptor(RequestInterceptor())  # returns None → no-op
        req = _req()
        result = chain.process_request(req)
        assert result is req

    def test_process_request_modifying_interceptor(self):
        chain = InterceptorChain()

        class HeaderAdder(RequestInterceptor):
            def intercept(self, ctx):
                ctx.request.headers["X-Custom"] = "yes"
                return InterceptorResult.replace(ctx.request)

        chain.add_request_interceptor(HeaderAdder())
        req = _req()
        result = chain.process_request(req)
        assert result.headers.get("X-Custom") == "yes"

    def test_process_request_skip_when_cant_intercept(self):
        chain = InterceptorChain()

        class NeverIntercepts(RequestInterceptor):
            def can_intercept(self, req):
                return False
            def intercept(self, ctx):
                raise AssertionError("should not be called")

        chain.add_request_interceptor(NeverIntercepts())
        req = _req()
        chain.process_request(req)  # must not raise

    def test_process_response_no_interceptors(self):
        chain = InterceptorChain()
        req = _req()
        resp = _resp()
        result = chain.process_response(req, resp)
        assert result is resp

    def test_process_response_passthrough(self):
        chain = InterceptorChain()
        chain.add_response_interceptor(ResponseInterceptor())
        req = _req()
        resp = _resp()
        result = chain.process_response(req, resp)
        assert result is resp

    def test_process_response_skip_when_cant_intercept(self):
        chain = InterceptorChain()

        class NeverIntercepts(ResponseInterceptor):
            def can_intercept(self, resp):
                return False
            def intercept(self, ctx):
                raise AssertionError("should not be called")

        chain.add_response_interceptor(NeverIntercepts())
        req = _req()
        chain.process_response(req, _resp())  # must not raise

    def test_process_response_modifying_interceptor(self):
        chain = InterceptorChain()

        class StatusChanger(ResponseInterceptor):
            def intercept(self, ctx):
                ctx.response.status_code = 999
                return InterceptorResult.replace(ctx.response)

        chain.add_response_interceptor(StatusChanger())
        req = _req()
        resp = _resp()
        result = chain.process_response(req, resp)
        assert result.status_code == 999

    def test_process_error_passthrough(self):
        chain = InterceptorChain()
        chain.add_error_interceptor(ErrorInterceptor())
        req = _req()
        err = ValueError("test error")
        result = chain.process_error(req, err)
        assert result is err

    def test_process_error_suppress(self):
        chain = InterceptorChain()

        class SuppressAll(ErrorInterceptor):
            def intercept(self, ctx):
                return InterceptorResult.suppress()

        chain.add_error_interceptor(SuppressAll())
        req = _req()
        result = chain.process_error(req, ValueError("boom"))
        assert result is None

    def test_process_error_no_interceptors_returns_error(self):
        chain = InterceptorChain()
        req = _req()
        err = ValueError("whoops")
        result = chain.process_error(req, err)
        assert result is err

    def test_process_error_skip_when_cant_intercept(self):
        chain = InterceptorChain()

        class NeverHandles(ErrorInterceptor):
            def can_intercept(self, error, request):
                return False
            def intercept(self, ctx):
                raise AssertionError("should not be called")

        chain.add_error_interceptor(NeverHandles())
        req = _req()
        err = ValueError("oops")
        result = chain.process_error(req, err)
        assert result is err


# ── RequestResponseLogger ─────────────────────────────────────────────────────

class TestRequestResponseLogger:

    def test_log_request_basic(self, caplog):
        logger = RequestResponseLogger("test.rr")
        req = _req(url="https://api.example.com/users")
        with caplog.at_level(logging.INFO, logger="test.rr"):
            logger.log_request(req)
        assert any("request_sent" in r.message for r in caplog.records)

    def test_log_request_redacts_sensitive_headers(self, caplog):
        logger = RequestResponseLogger("test.rr2")
        req = _req()
        req.headers = {
            "Authorization": "Bearer secret_token",
            "X-Api-Key": "mysecret",
            "Content-Type": "application/json",
        }
        with caplog.at_level(logging.INFO, logger="test.rr2"):
            logger.log_request(req, include_body=True)
        logged = caplog.records[-1].message
        data = json.loads(logged)
        assert data["headers"]["Authorization"] == "[REDACTED]"
        assert data["headers"]["X-Api-Key"] == "[REDACTED]"
        assert data["headers"]["Content-Type"] == "application/json"

    def test_log_request_include_body(self, caplog):
        logger = RequestResponseLogger("test.rr3")
        req = _req(method="POST")
        req.body = '{"key": "value"}'
        with caplog.at_level(logging.INFO, logger="test.rr3"):
            logger.log_request(req, include_body=True)
        logged = json.loads(caplog.records[-1].message)
        assert "body" in logged

    def test_log_response_basic(self, caplog):
        logger = RequestResponseLogger("test.rr4")
        req = _req()
        resp = _resp(status=200)
        with caplog.at_level(logging.INFO, logger="test.rr4"):
            logger.log_response(req, resp, elapsed_time=0.5)
        data = json.loads(caplog.records[-1].message)
        assert data["event"] == "response_received"
        assert data["status_code"] == 200
        assert data["elapsed_time_seconds"] == 0.5

    def test_log_response_include_body(self, caplog):
        logger = RequestResponseLogger("test.rr5")
        req = _req()
        resp = _resp()
        resp.body = "response body text"
        with caplog.at_level(logging.INFO, logger="test.rr5"):
            logger.log_response(req, resp, elapsed_time=0.1, include_body=True)
        data = json.loads(caplog.records[-1].message)
        assert "body" in data

    def test_log_error_basic(self, caplog):
        logger = RequestResponseLogger("test.rr6")
        req = _req()
        err = ConnectionError("refused")
        with caplog.at_level(logging.ERROR, logger="test.rr6"):
            logger.log_error(req, err)
        data = json.loads(caplog.records[-1].message)
        assert data["event"] == "request_failed"
        assert data["error_type"] == "ConnectionError"


# ── Built-in logging interceptors ─────────────────────────────────────────────

class TestLoggingInterceptors:

    def test_logging_request_interceptor_uses_default_logger(self):
        i = LoggingRequestInterceptor()
        assert i.logger is not None

    def test_logging_request_interceptor_with_custom_logger(self):
        custom = RequestResponseLogger("custom")
        i = LoggingRequestInterceptor(logger=custom)
        assert i.logger is custom

    def test_logging_request_interceptor_intercept_returns_continue(self, caplog):
        i = LoggingRequestInterceptor()
        req = _req()
        ctx = InterceptorContext(request=req)
        result = i.intercept(ctx)
        assert result.action == InterceptorAction.CONTINUE

    def test_logging_response_interceptor_uses_default_logger(self):
        i = LoggingResponseInterceptor()
        assert i.logger is not None

    def test_logging_response_interceptor_intercept_returns_none(self):
        i = LoggingResponseInterceptor()
        req = _req()
        resp = _resp()
        ctx = InterceptorContext(request=req, response=resp)
        result = i.intercept(ctx)
        assert result.action == InterceptorAction.CONTINUE

    def test_logging_error_interceptor_uses_default_logger(self):
        i = LoggingErrorInterceptor()
        assert i.logger is not None

    def test_logging_error_interceptor_intercept_returns_error(self):
        i = LoggingErrorInterceptor()
        req = _req()
        err = RuntimeError("boom")
        ctx = InterceptorContext(request=req, error=err)
        result = i.intercept(ctx)
        assert result.action == InterceptorAction.CONTINUE
        #TODO: Assert that error was logged

    def test_logging_error_interceptor_with_custom_logger(self):
        custom = RequestResponseLogger("custom.err")
        i = LoggingErrorInterceptor(logger=custom)
        assert i.logger is custom
