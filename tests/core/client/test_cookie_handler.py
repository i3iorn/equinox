from __future__ import annotations

from typing import Any, cast

from equinox.core.client.cookie_handler import CookieHandler
from equinox.core.request import Request, Response


class _ManagerWithRecords:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def to_httpx_cookies(self) -> dict[str, str]:
        return {"session": "abc"}

    def to_httpx_cookie_records(self) -> list[dict[str, str]]:
        return [{"name": "session", "value": "abc", "domain": "example.com", "path": "/"}]

    def update_from_set_cookie_headers(self, headers: list[str], url: str) -> None:
        self.calls.append(("repeat", headers, url))

    def update_from_response(self, headers: dict[str, str], url: str) -> None:
        self.calls.append(("single", headers, url))


class _ManagerWithoutRecords:
    def to_httpx_cookies(self) -> dict[str, str]:
        return {"token": "xyz"}


class _ManagerThatFails(_ManagerWithRecords):
    def update_from_response(self, headers: dict[str, str], url: str) -> None:
        raise RuntimeError("cookie parse failed")


def _make_response(
    headers: dict[str, str],
    set_cookie_headers: list[str] | None = None,
) -> Response:
    return Response(
        status_code=200,
        reason="OK",
        headers=headers,
        body=b"{}",
        elapsed=0.01,
        request=Request(method="GET", url="https://example.com"),
        set_cookie_headers=set_cookie_headers,
    )


def test_cookie_handler_without_manager_is_noop() -> None:
    handler = CookieHandler(None)

    assert handler.get_httpx_cookies() == {}
    assert handler.get_httpx_cookie_records() == []

    handler.update_from_response(None, "https://example.com")
    handler.update_from_response(_make_response({"set-cookie": "a=1"}), "https://example.com")

    assert repr(handler) == "CookieHandler(managed=False)"


def test_cookie_handler_records_and_repeated_set_cookie_path() -> None:
    manager = _ManagerWithRecords()
    handler = CookieHandler(cast(Any, manager))

    assert handler.get_httpx_cookies() == {"session": "abc"}
    assert handler.get_httpx_cookie_records()[0]["domain"] == "example.com"

    handler.update_from_response(
        _make_response({}, set_cookie_headers=["a=1; Path=/", "b=2; Path=/"]),
        "https://example.com",
    )

    assert manager.calls[0][0] == "repeat"
    assert manager.calls[0][1] == ["a=1; Path=/", "b=2; Path=/"]
    assert repr(handler) == "CookieHandler(managed=True)"


def test_cookie_handler_fallback_record_generation() -> None:
    handler = CookieHandler(cast(Any, _ManagerWithoutRecords()))
    records = handler.get_httpx_cookie_records()

    assert records == [{"name": "token", "value": "xyz", "domain": "", "path": "/"}]


def test_cookie_handler_single_set_cookie_path() -> None:
    manager = _ManagerWithRecords()
    handler = CookieHandler(cast(Any, manager))

    handler.update_from_response(
        _make_response({"Set-Cookie": "session=abc; Path=/"}),
        "https://example.com",
    )

    assert manager.calls[0][0] == "single"
    assert "Set-Cookie" in manager.calls[0][1]


def test_cookie_handler_swallows_update_exceptions() -> None:
    handler = CookieHandler(cast(Any, _ManagerThatFails()))

    # Should not raise when manager update fails.
    handler.update_from_response(_make_response({"set-cookie": "x=1"}), "https://example.com")
