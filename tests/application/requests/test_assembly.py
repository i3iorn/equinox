"""Coverage tests for equinox.application.requests._assembly."""

from __future__ import annotations

import json

import pytest

from equinox.application.requests._assembly import (
    _MAX_BODY_SIZE,
    _assemble_graphql_body,
    assemble_body,
    inject_content_type,
)

# ── assemble_body ──────────────────────────────────────────────────────────────


class TestAssembleBodyMultipart:
    def test_multipart_returns_none_body_with_filtered_rows(self) -> None:
        rows = [
            {"key": "file", "value": "data"},
            {"key": "", "value": "ignored"},
            {"key": "  ", "value": "also ignored"},
        ]
        body, multipart = assemble_body("multipart/form-data", "", "", "", rows)
        assert body is None
        assert multipart == [{"key": "file", "value": "data"}]

    def test_multipart_empty_rows_returns_empty_list(self) -> None:
        body, multipart = assemble_body("multipart/form-data", "", "", "", [])
        assert body is None
        assert multipart == []


class TestAssembleBodyGraphQL:
    def test_graphql_without_variables(self) -> None:
        body, multipart = assemble_body("GraphQL", "", "{ user { id } }", "", [])
        assert multipart is None
        assert body is not None
        parsed = json.loads(body)
        assert parsed["query"] == "{ user { id } }"
        assert "variables" not in parsed

    def test_graphql_with_valid_variables(self) -> None:
        vars_json = '{"id": 42}'
        body, multipart = assemble_body("GraphQL", "", "{ user { id } }", vars_json, [])
        assert multipart is None
        parsed = json.loads(body)
        assert parsed["variables"] == {"id": 42}

    def test_graphql_with_invalid_variables_raises(self) -> None:
        with pytest.raises(ValueError, match="GraphQL variables must be valid JSON"):
            assemble_body("GraphQL", "", "{ user { id } }", "{bad-json}", [])


class TestAssembleBodyNoneAndEmpty:
    def test_body_type_none_returns_none_none(self) -> None:
        body, multipart = assemble_body("none", "some text", "", "", [])
        assert body is None
        assert multipart is None

    def test_empty_body_text_returns_none_none(self) -> None:
        body, multipart = assemble_body("raw (JSON)", "", "", "", [])
        assert body is None
        assert multipart is None


class TestAssembleBodySizeLimit:
    def test_body_exceeding_max_size_raises(self) -> None:
        oversized = "x" * (_MAX_BODY_SIZE + 1)
        with pytest.raises(ValueError, match="exceeds maximum"):
            assemble_body("raw (JSON)", oversized, "", "", [])

    def test_body_at_exact_max_is_accepted(self) -> None:
        at_limit = "x" * _MAX_BODY_SIZE
        body, _ = assemble_body("raw (JSON)", at_limit, "", "", [])
        assert body == at_limit


class TestAssembleBodyPlainText:
    def test_plain_body_returned_as_is(self) -> None:
        body, multipart = assemble_body("raw (JSON)", '{"key":"val"}', "", "", [])
        assert body == '{"key":"val"}'
        assert multipart is None


# ── _assemble_graphql_body ─────────────────────────────────────────────────────


class TestAssembleGraphQLBody:
    def test_empty_variables_string_omitted(self) -> None:
        result = _assemble_graphql_body("{ ping }", "")
        parsed = json.loads(result)
        assert "variables" not in parsed

    def test_whitespace_variables_string_omitted(self) -> None:
        result = _assemble_graphql_body("{ ping }", "   ")
        parsed = json.loads(result)
        assert "variables" not in parsed

    def test_valid_variables_included(self) -> None:
        result = _assemble_graphql_body("{ user(id: $id) { name } }", '{"id": 1}')
        parsed = json.loads(result)
        assert parsed["variables"] == {"id": 1}

    def test_invalid_variables_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="GraphQL variables must be valid JSON"):
            _assemble_graphql_body("{ ping }", "not-json")


# ── inject_content_type ────────────────────────────────────────────────────────


class TestInjectContentType:
    def test_no_body_returns_headers_unchanged(self) -> None:
        headers = {"X-Custom": "value"}
        result = inject_content_type(None, "raw (JSON)", headers)
        assert result is headers

    def test_empty_body_returns_headers_unchanged(self) -> None:
        headers = {"X-Custom": "value"}
        result = inject_content_type("", "raw (JSON)", headers)
        assert result is headers

    def test_content_type_already_set_not_overwritten(self) -> None:
        headers = {"content-type": "text/plain"}
        result = inject_content_type('{"a":1}', "raw (JSON)", headers)
        assert result["content-type"] == "text/plain"
        assert "Content-Type" not in result

    def test_content_type_set_correctly_for_json(self) -> None:
        result = inject_content_type('{"a":1}', "raw (JSON)", {})
        assert result["Content-Type"] == "application/json"

    def test_content_type_set_for_xml(self) -> None:
        result = inject_content_type("<xml/>", "raw (XML)", {})
        assert result["Content-Type"] == "application/xml"

    def test_content_type_set_for_form_urlencoded(self) -> None:
        result = inject_content_type("a=1&b=2", "form-urlencoded", {})
        assert result["Content-Type"] == "application/x-www-form-urlencoded"

    def test_unknown_body_type_returns_headers_unchanged(self) -> None:
        headers = {"X-Custom": "value"}
        result = inject_content_type("some body", "binary", headers)
        assert result is headers

    def test_original_headers_dict_not_mutated(self) -> None:
        original = {"X-Custom": "value"}
        result = inject_content_type('{"a":1}', "raw (JSON)", original)
        assert "Content-Type" not in original
        assert result is not original
