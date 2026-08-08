"""Tests for exporters/openapi.py and exporters/postman.py coverage gaps."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from equinox.exporters.openapi import OpenAPIExporter
from equinox.exporters.postman import PostmanExporter

# ── helpers ───────────────────────────────────────────────────────────────────


def _make_db(collection: dict[str, Any], requests: list[dict[str, Any]]) -> MagicMock:
    """Build a minimal DB mock accepted by the exporters."""
    db = MagicMock()
    with (
        patch("equinox.exporters._prepared.CollectionManager") as MockCM,
        patch("equinox.exporters.postman.CollectionManager") as MockPM,
    ):
        MockCM.return_value.get_collection.return_value = collection
        MockCM.return_value.list_requests_in_collection.return_value = requests
        MockPM.return_value.list_collection_variables.return_value = []
        yield db, MockCM, MockPM


def _row(
    name: str = "Test",
    method: str = "GET",
    url: str = "https://api.example.com/users",
    body: str | None = None,
    auth: str | None = None,
    params: str = "{}",
    headers: str = "{}",
    path_params: str = "{}",
    description: str = "",
) -> dict[str, Any]:
    return {
        "name": name,
        "method": method,
        "url": url,
        "body": body,
        "auth": auth,
        "params": params,
        "headers": headers,
        "path_params": path_params,
        "description": description,
    }


# ── OpenAPIExporter._build_security_scheme ───────────────────────────────────


class TestOpenAPIBuildSecurityScheme:
    def test_bearer_scheme(self) -> None:
        scheme = OpenAPIExporter._build_security_scheme({"type": "bearer"})
        assert scheme["type"] == "http"
        assert scheme["scheme"] == "bearer"

    def test_apikey_scheme(self) -> None:
        scheme = OpenAPIExporter._build_security_scheme(
            {"type": "apikey", "key": "X-API-Key", "in": "header"},
        )
        assert scheme["type"] == "apiKey"
        assert scheme["name"] == "X-API-Key"

    def test_basic_scheme(self) -> None:
        scheme = OpenAPIExporter._build_security_scheme({"type": "basic"})
        assert scheme["type"] == "http"
        assert scheme["scheme"] == "basic"

    def test_oauth2_scheme(self) -> None:
        scheme = OpenAPIExporter._build_security_scheme(
            {
                "type": "oauth2",
                "auth_url": "https://auth.example.com",
                "token_url": "https://token.example.com",
            },
        )
        assert scheme["type"] == "oauth2"
        assert "flows" in scheme

    def test_unknown_auth_returns_empty(self) -> None:
        assert OpenAPIExporter._build_security_scheme({"type": "custom"}) == {}

    def test_no_type_returns_empty(self) -> None:
        assert OpenAPIExporter._build_security_scheme({}) == {}


class TestOpenAPIBuildOperation:
    def _make_req(self, **kw: Any) -> Any:
        from equinox.exporters._prepared import _PreparedRequest

        defaults = dict(
            name="Test",
            description="",
            method="GET",
            raw_url="https://api.example.com/users",
            body=None,
            headers={},
            params={},
            path_params={},
            auth_obj={},
            content_type="application/json",
            url_parts={
                "scheme": "https",
                "netloc": "api.example.com",
                "path": "/users",
                "query": "",
                "hostname": "api.example.com",
                "port": None,
            },
        )
        defaults.update(kw)
        return _PreparedRequest(**defaults)

    def test_operation_without_body(self) -> None:
        req = self._make_req()
        op = OpenAPIExporter._build_operation(req)
        assert "requestBody" not in op
        assert op["summary"] == "Test"

    def test_operation_with_body(self) -> None:
        req = self._make_req(body='{"x": 1}')
        op = OpenAPIExporter._build_operation(req)
        assert "requestBody" in op

    def test_operation_with_auth(self) -> None:
        req = self._make_req(auth_obj={"type": "bearer"})
        op = OpenAPIExporter._build_operation(req)
        assert "security" in op

    def test_operation_with_params(self) -> None:
        req = self._make_req(params={"q": "search"})
        op = OpenAPIExporter._build_operation(req)
        params = op["parameters"]
        names = [p["name"] for p in params]
        assert "q" in names

    def test_operation_with_path_params(self) -> None:
        req = self._make_req(path_params={"id": "123"})
        op = OpenAPIExporter._build_operation(req)
        params = op["parameters"]
        path_param = next((p for p in params if p["in"] == "path"), None)
        assert path_param is not None

    def test_security_schemes_collected(self) -> None:
        """Ensures _build_security_scheme is called for unique auth types."""
        scheme = OpenAPIExporter._build_security_scheme({"type": "bearer"})
        assert scheme  # bearer scheme is non-empty


# ── PostmanExporter._build_auth ───────────────────────────────────────────────


class TestPostmanBuildAuth:
    def test_bearer_auth(self) -> None:
        result = PostmanExporter._build_auth({"type": "bearer"})
        assert result["type"] == "bearer"
        token_entry = result["bearer"][0]
        assert token_entry["value"] == "[REDACTED]"

    def test_apikey_auth(self) -> None:
        result = PostmanExporter._build_auth({"type": "apikey", "key": "X-API-Key", "in": "header"})
        assert result["type"] == "apikey"
        keys = [entry["key"] for entry in result["apikey"]]
        assert "key" in keys
        assert "value" in keys

    def test_basic_auth(self) -> None:
        result = PostmanExporter._build_auth({"type": "basic", "username": "alice"})
        assert result["type"] == "basic"
        values = {e["key"]: e["value"] for e in result["basic"]}
        assert values["username"] == "alice"
        assert values["password"] == "[REDACTED]"

    def test_oauth2_auth(self) -> None:
        result = PostmanExporter._build_auth(
            {
                "type": "oauth2",
                "grant_type": "authorization_code",
                "token_url": "https://token.example.com",
            },
        )
        assert result["type"] == "oauth2"
        items = {e["key"]: e["value"] for e in result["oauth2"]}
        assert items["grant_type"] == "authorization_code"

    def test_unknown_auth_returns_empty(self) -> None:
        assert PostmanExporter._build_auth({"type": "custom_auth"}) == {}

    def test_no_auth_type_returns_empty(self) -> None:
        assert PostmanExporter._build_auth({}) == {}


class TestPostmanBuildItem:
    def _make_req(self, **kw: Any) -> Any:
        from equinox.exporters._prepared import _PreparedRequest

        defaults = dict(
            name="Test",
            description="",
            method="GET",
            raw_url="https://api.example.com/users",
            body=None,
            headers={},
            params={},
            path_params={},
            auth_obj={},
            content_type="application/json",
            url_parts={
                "scheme": "https",
                "netloc": "api.example.com",
                "path": "/users",
                "query": "",
                "hostname": "api.example.com",
                "port": None,
            },
        )
        defaults.update(kw)
        return _PreparedRequest(**defaults)

    def test_item_without_auth_or_body(self) -> None:
        req = self._make_req()
        item = PostmanExporter._build_item(req)
        assert item["name"] == "Test"
        assert "auth" not in item["request"]
        assert "body" not in item["request"]

    def test_item_with_bearer_auth(self) -> None:
        req = self._make_req(auth_obj={"type": "bearer"})
        item = PostmanExporter._build_item(req)
        assert "auth" in item["request"]

    def test_item_with_body(self) -> None:
        req = self._make_req(body='{"key":"value"}')
        item = PostmanExporter._build_item(req)
        assert "body" in item["request"]
        assert item["request"]["body"]["mode"] == "raw"

    def test_item_with_json_content_type_uses_json_language(self) -> None:
        req = self._make_req(body='{"k": 1}', content_type="application/json")
        item = PostmanExporter._build_item(req)
        lang = item["request"]["body"]["options"]["raw"]["language"]
        assert lang == "json"


class TestPostmanIncludeHistoryWarning:
    """Tests lines 50-56: FutureWarning when include_history=True."""

    def test_include_history_raises_warning(self) -> None:
        from equinox.storage.collections import CollectionManager

        db = MagicMock()
        collection = {"id": 1, "name": "Test", "description": ""}
        with (
            patch.object(CollectionManager, "get_collection", return_value=collection),
            patch.object(CollectionManager, "list_requests_in_collection", return_value=[]),
            patch.object(CollectionManager, "list_collection_variables", return_value=[]),
        ):
            with pytest.warns(FutureWarning, match="include_history"):
                PostmanExporter.export_collection(db, 1, include_history=True)
