"""Tests for Plugin base class lifecycle and hook behavior."""

from __future__ import annotations

import pytest

from equinox.core.request import Request, Response
from equinox.plugins.base import AuthPlugin, Plugin, PluginContext, TransformPlugin


def _make_response() -> Response:
    req = Request(method="GET", url="https://example.com")
    return Response(
        status_code=200,
        reason="OK",
        headers={},
        body=b"",
        elapsed=0.01,
        request=req,
    )


class ConcretePlugin(Plugin):
    @property
    def name(self) -> str:
        return "concrete"

    @property
    def version(self) -> str:
        return "1.0"


class TestPluginBase:
    def test_abstract_without_name_or_version(self) -> None:
        ctx = PluginContext(storage=None, http_client=None, config={})
        with pytest.raises(TypeError):
            Plugin(ctx)  # type: ignore[abstract]

    def test_concrete_plugin_defaults(self) -> None:
        ctx = PluginContext(storage=None, http_client=None, config={})
        p = ConcretePlugin(ctx)
        assert p.name == "concrete"
        assert p.version == "1.0"
        assert p.description == ""
        assert p.enabled is True
        assert p.sandbox is None

    def test_on_request_returns_none_by_default(self) -> None:
        ctx = PluginContext(storage=None, http_client=None, config={})
        p = ConcretePlugin(ctx)
        req = Request(method="GET", url="https://example.com")
        assert p.on_request(req) is None

    def test_on_response_returns_none_by_default(self) -> None:
        ctx = PluginContext(storage=None, http_client=None, config={})
        p = ConcretePlugin(ctx)
        req = Request(method="GET", url="https://example.com")
        resp = _make_response()
        assert p.on_response(req, resp) is None

    def test_on_error_does_not_raise(self) -> None:
        ctx = PluginContext(storage=None, http_client=None, config={})
        p = ConcretePlugin(ctx)
        req = Request(method="GET", url="https://example.com")
        p.on_error(req, RuntimeError("fail"))  # should not raise


class TestAuthPlugin:
    def test_must_implement_apply_auth(self) -> None:
        ctx = PluginContext(storage=None, http_client=None, config={})
        with pytest.raises(TypeError):
            AuthPlugin(ctx)  # type: ignore[abstract]


class TestTransformPlugin:
    def test_transform_request_identity(self) -> None:
        ctx = PluginContext(storage=None, http_client=None, config={})

        class MyTransform(TransformPlugin):
            @property
            def name(self) -> str:
                return "t"

            @property
            def version(self) -> str:
                return "1"

        t = MyTransform(ctx)
        req = Request(method="POST", url="https://example.com")
        assert t.transform_request(req) is req

    def test_transform_response_identity(self) -> None:
        ctx = PluginContext(storage=None, http_client=None, config={})

        class MyTransform(TransformPlugin):
            @property
            def name(self) -> str:
                return "t"

            @property
            def version(self) -> str:
                return "1"

        t = MyTransform(ctx)
        resp = _make_response()
        assert t.transform_response(resp) is resp
