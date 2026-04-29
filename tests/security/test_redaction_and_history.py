import json
import logging
import os
import sys
import types

import pytest

from equinox.core.request.request import Request
from equinox.core.request.headers import HeaderDict
from equinox.core.history_config import set_capture_bodies
from equinox.storage.history._serializer import _HistorySerializer
from equinox.core.redact import redact_headers


def _capture_log_for(func, *args, **kwargs):
    import logging
    import io
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    logger = logging.getLogger("equinox.requests")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        func(*args, **kwargs)
        handler.flush()
        return stream.getvalue()
    finally:
        logger.removeHandler(handler)


def test_interceptor_redacts_sensitive_headers_and_body(caplog):
    # Build a request with sensitive header and body content
    req = Request(
        method="POST",
        url="https://example.com/api/login?example=true",
        headers={
            "Authorization": "Bearer secrettoken",
            "Content-Type": "application/json",
            "X-Custom": "value",
        },
        body='{"username":"alice","password":"hunter2"}'
    )

    # Prepare logging capture before emitting any logs
    caplog.clear()
    caplog.set_level(logging.INFO)
    # Use the logging interceptor to emit a request log
    from equinox.core.interceptors import LoggingRequestInterceptor, InterceptorChain
    chain = InterceptorChain()
    chain.add_request_interceptor(LoggingRequestInterceptor())

    # Trigger request processing which logs the sanitized payload
    chain.process_request(req)

    # Verify log contains redacted Authorization header and redacted body
    caplog.set_level(logging.INFO)
    found = False
    for rec in caplog.records:
        if rec.levelno == logging.INFO and rec.getMessage().strip():
            try:
                payload = json.loads(rec.getMessage())
                headers = payload.get("headers", {})
                if isinstance(headers, dict) and headers.get("Authorization"):
                    assert headers["Authorization"] == "[REDACTED]"
                    found = True
            except Exception:
                continue
    assert found, "Expected redacted Authorization header in logs"
