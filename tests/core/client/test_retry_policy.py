from __future__ import annotations

from typing import Dict, List, Optional

import pytest

from equinox.core.client.retry_policy import RetryPolicy
from equinox.core.exceptions import RequestTimeoutError
from equinox.core.request import Request, Response


def _make_response(status_code: int, headers: Optional[Dict[str, str]] = None) -> Response:
    return Response(
        status_code=status_code,
        reason="OK",
        headers=headers or {},
        body=b"{}",
        elapsed=0.01,
        request=Request(method="GET", url="https://example.com"),
    )


def test_init_rejects_non_positive_retry_after_cap() -> None:
    with pytest.raises(ValueError):
        RetryPolicy(timeout_retries=1, http_retries=0, retry_after_cap_seconds=0)


def test_execute_retries_timeout_and_records_summary() -> None:
    sleeps: List[float] = []
    policy = RetryPolicy(
        timeout_retries=3,
        http_retries=0,
        interruptible_sleep=lambda s: sleeps.append(s),
    )

    calls = {"n": 0}

    def _func() -> Response:
        calls["n"] += 1
        if calls["n"] < 3:
            raise RequestTimeoutError("timeout")
        return _make_response(200)

    response = policy.execute(_func)

    assert response.status_code == 200
    assert calls["n"] == 3
    assert sleeps == [1, 2]
    assert policy.get_retry_summary() == "retried 2\u00d7 after timeout"


def test_execute_raises_on_final_timeout() -> None:
    policy = RetryPolicy(timeout_retries=2, http_retries=0, interruptible_sleep=lambda _: None)

    with pytest.raises(RequestTimeoutError):
        policy.execute(lambda: (_ for _ in ()).throw(RequestTimeoutError("always timeout")))


def test_execute_with_http_overload_honors_retry_after_and_cap() -> None:
    sleeps: List[float] = []
    policy = RetryPolicy(
        timeout_retries=1,
        http_retries=3,
        retry_after_cap_seconds=10,
        interruptible_sleep=lambda s: sleeps.append(s),
    )

    responses = [
        _make_response(429, {"retry-after": "120"}),
        _make_response(503, {"retry-after": "-1"}),
        _make_response(200),
    ]

    def _func() -> Response:
        return responses.pop(0)

    response = policy.execute_with_http_overload(_func)

    assert response.status_code == 200
    assert sleeps == [10.0, 1.0]
    # Summary uses first observed status when multiple are present.
    assert policy.get_retry_summary() == "retried 2\u00d7 after 429"


def test_parse_retry_after_defaults_for_missing_or_invalid_headers() -> None:
    policy = RetryPolicy(timeout_retries=1, http_retries=1, retry_after_cap_seconds=5)

    assert policy._parse_retry_after(_make_response(429, {})) == 1.0
    assert policy._parse_retry_after(_make_response(429, {"retry-after": "not-a-number"})) == 1.0
    assert policy._parse_retry_after(_make_response(429, {"retry-after": "2.5"})) == 2.5


def test_repr_eq_hash_and_notimplemented() -> None:
    p1 = RetryPolicy(timeout_retries=2, http_retries=1)
    p2 = RetryPolicy(timeout_retries=2, http_retries=1)
    p3 = RetryPolicy(timeout_retries=3, http_retries=1)

    assert p1 == p2
    assert p1 != p3
    assert hash(p1) == hash(p2)
    assert "timeout_retries=2" in repr(p1)
    assert p1.__eq__(object()) is NotImplemented
