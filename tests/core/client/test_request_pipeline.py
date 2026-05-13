"""Focused tests for request pipeline orchestration and error handling."""

from __future__ import annotations

import pytest

from equinox.core.client.pipeline import RequestPipeline
from equinox.core.exceptions import RequestError
from equinox.core.interceptors._base import (
    ErrorInterceptor,
    InterceptorResult,
    RequestInterceptor,
    ResponseInterceptor,
)
from equinox.core.interceptors.chain import InterceptorChain
from equinox.core.request import Request, Response


class _AuditRecorder:
    def __init__(self) -> None:
        self.events = []

    def log_request(self, method, url, *, status_code=None, error=None) -> None:
        self.events.append(
            {
                "method": method,
                "url": url,
                "status_code": status_code,
                "error": error,
            }
        )


class _ReplaceUrlInterceptor(RequestInterceptor):
    def intercept(self, context):
        new_req = Request(
            method=context.request.method,
            url="https://example.com/replaced",
            headers=dict(context.request.headers),
            params=dict(context.request.params),
            body=context.request.body,
        )
        return InterceptorResult.replace(new_req)


class _ReplaceReasonInterceptor(ResponseInterceptor):
    def intercept(self, context):
        response = context.response
        patched = Response(
            status_code=response.status_code,
            reason="patched",
            headers=dict(response.headers),
            body=response.body,
            elapsed=response.elapsed,
            request=context.request,
        )
        return InterceptorResult.replace(patched)


class _SuppressAllErrors(ErrorInterceptor):
    def intercept(self, context):
        return InterceptorResult.suppress()


def test_pipeline_runs_request_and_response_interceptors_and_audits_success() -> None:
    request = Request(method="GET", url="https://example.com/original")
    chain = InterceptorChain()
    chain.add_request_interceptor(_ReplaceUrlInterceptor())
    chain.add_response_interceptor(_ReplaceReasonInterceptor())

    audit = _AuditRecorder()
    seen = {}

    def _dispatch(req: Request) -> Response:
        seen["url"] = req.url
        return Response(
            status_code=200,
            reason="ok",
            headers={"content-type": "application/json"},
            body=b"{}",
            elapsed=0.01,
            request=req,
        )

    pipeline = RequestPipeline(chain, audit, error_handlers=[])
    response = pipeline.execute(request, _dispatch)

    assert seen["url"] == "https://example.com/replaced"
    assert response.reason == "patched"
    assert len(audit.events) == 1
    assert audit.events[0]["status_code"] == 200
    assert audit.events[0]["error"] is None


def test_pipeline_uses_registered_error_mapper_and_emits_audit_error() -> None:
    request = Request(method="GET", url="https://example.com/error")
    chain = InterceptorChain()
    audit = _AuditRecorder()

    def _handler(exc: Exception, req: Request):
        return {
            "error": RequestError("mapped failure"),
            "audit_tag": "MappedError",
            "log_message": f"mapped: {type(exc).__name__}",
        }

    pipeline = RequestPipeline(chain, audit, error_handlers=[(ValueError, _handler)])

    def _dispatch(_req: Request) -> Response:
        raise ValueError("boom")

    with pytest.raises(RequestError, match="mapped failure"):
        pipeline.execute(request, _dispatch)

    assert len(audit.events) == 1
    assert audit.events[0]["error"] == "MappedError"


def test_pipeline_raises_sentinel_error_when_error_interceptor_suppresses() -> None:
    request = Request(method="GET", url="https://example.com/error")
    chain = InterceptorChain()
    chain.add_error_interceptor(_SuppressAllErrors())
    audit = _AuditRecorder()

    pipeline = RequestPipeline(chain, audit, error_handlers=[])

    def _dispatch(_req: Request) -> Response:
        raise RuntimeError("transport down")

    with pytest.raises(RequestError, match="suppressed"):
        pipeline.execute(request, _dispatch)

    assert len(audit.events) == 1
    assert audit.events[0]["error"] == "RuntimeError"

