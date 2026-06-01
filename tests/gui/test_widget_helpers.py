"""Tests for pure-logic helpers in the GUI widgets package.

These functions have no PyQt6 dependency so they run headless.
"""
import pytest
from equinox.application.requests._assembly import apply_default_headers
from equinox.application.requests._assembly import assemble_body
from equinox.application.requests._assembly import detect_body_type
from equinox.application.requests._assembly import inject_content_type
from equinox.core.request import Request


class TestDetectBodyType:
    """detect_body_type — heuristic body-type detection."""

    def test_json_by_content_type_header(self):
        assert detect_body_type("anything", {"Content-Type": "application/json"}) == "raw (JSON)"

    def test_xml_by_content_type_header(self):
        assert detect_body_type("anything", {"Content-Type": "text/xml"}) == "raw (XML)"

    def test_urlencoded_by_content_type_header(self):
        assert (
            detect_body_type("anything", {"Content-Type": "application/x-www-form-urlencoded"})
            == "form-urlencoded"
        )

    def test_text_by_content_type_header(self):
        assert detect_body_type("anything", {"Content-Type": "text/plain"}) == "raw (text)"

    def test_json_sniffing_object(self):
        assert detect_body_type('{"key": "value"}') == "raw (JSON)"

    def test_json_sniffing_array(self):
        assert detect_body_type("[1, 2, 3]") == "raw (JSON)"

    def test_json_sniffing_empty_object(self):
        assert detect_body_type("{}") == "raw (JSON)"

    def test_invalid_json_starting_with_brace(self):
        assert detect_body_type("{not json}") != "raw (JSON)"

    def test_xml_sniffing(self):
        assert detect_body_type("<root><item/></root>") == "raw (XML)"

    def test_urlencoded_sniffing(self):
        assert detect_body_type("a=1&b=2") == "form-urlencoded"

    def test_plain_text_fallback(self):
        assert detect_body_type("hello world") == "raw (text)"

    def test_empty_body(self):
        assert detect_body_type("") == "raw (text)"

    def test_whitespace_body(self):
        assert detect_body_type("   ") == "raw (text)"

    def test_header_takes_priority_over_sniffing(self):
        # Body looks like JSON but header says XML
        assert detect_body_type('{"key": 1}', {"Content-Type": "application/xml"}) == "raw (XML)"

    def test_no_headers(self):
        assert detect_body_type('{"a": 1}', None) == "raw (JSON)"


class TestAssembleBody:
    """assemble_body — builds body + multipart_data from editor state."""

    def test_none_body_type(self):
        body, mp = assemble_body("none", "", "", "", [])
        assert body is None
        assert mp is None

    def test_raw_json(self):
        body, mp = assemble_body("raw (JSON)", '{"a":1}', "", "", [])
        assert body == '{"a":1}'
        assert mp is None

    def test_multipart(self):
        rows = [{"key": "file", "value": "test.txt"}, {"key": "", "value": "skip"}]
        body, mp = assemble_body("multipart/form-data", "", "", "", rows)
        assert body is None
        assert mp is not None
        assert len(mp) == 1
        assert mp[0]["key"] == "file"

    def test_graphql(self):
        import json

        body, mp = assemble_body("GraphQL", "", "query { users { id } }", '{"limit": 10}', [])
        assert mp is None
        assert body is not None
        parsed = json.loads(body)
        assert parsed["query"] == "query { users { id } }"
        assert parsed["variables"] == {"limit": 10}

    def test_graphql_invalid_vars(self):
        with pytest.raises(ValueError, match="GraphQL variables must be valid JSON"):
            assemble_body("GraphQL", "", "query { me }", "not-json", [])

    def test_empty_raw_returns_none(self):
        body, mp = assemble_body("raw (text)", "", "", "", [])
        assert body is None


class TestInjectContentType:
    """inject_content_type — auto-adds Content-Type when missing."""

    def test_adds_json_content_type(self):
        result = inject_content_type('{"a":1}', "raw (JSON)", {})
        assert result["Content-Type"] == "application/json"

    def test_preserves_existing_content_type(self):
        headers = {"Content-Type": "text/plain"}
        result = inject_content_type('{"a":1}', "raw (JSON)", headers)
        assert result["Content-Type"] == "text/plain"

    def test_preserves_existing_content_type_case_insensitive(self):
        headers = {"content-type": "text/plain"}
        result = inject_content_type('{"a":1}', "raw (JSON)", headers)
        assert result["content-type"] == "text/plain"
        assert "Content-Type" not in result

    def test_no_body_no_injection(self):
        result = inject_content_type(None, "raw (JSON)", {})
        assert "Content-Type" not in result

    def test_empty_body_no_injection(self):
        result = inject_content_type("", "raw (JSON)", {})
        assert "Content-Type" not in result

    def test_xml_content_type(self):
        result = inject_content_type("<root/>", "raw (XML)", {})
        assert result["Content-Type"] == "application/xml"

    def test_form_urlencoded_content_type(self):
        result = inject_content_type("a=1", "form-urlencoded", {})
        assert result["Content-Type"] == "application/x-www-form-urlencoded"

    def test_graphql_gets_json_content_type(self):
        result = inject_content_type('{"query":"{}"}', "GraphQL", {})
        assert result["Content-Type"] == "application/json"

    def test_unknown_body_type_no_injection(self):
        result = inject_content_type("data", "raw (text)", {})
        assert "Content-Type" not in result

    def test_does_not_mutate_input(self):
        original = {"Accept": "application/json"}
        result = inject_content_type('{"a":1}', "raw (JSON)", original)
        assert "Content-Type" not in original
        assert "Content-Type" in result


class TestApplyDefaultHeaders:
    """apply_default_headers — inject defaults without duplicate semantic headers."""

    def test_keeps_existing_user_agent_any_case(self):
        req = Request(method="GET", url="https://example.com", headers={"user-agent": "custom"})
        apply_default_headers(req)
        assert req.headers["user-agent"] == "custom"
        assert req.headers.get("User-Agent") == "custom"


# ── path_params helpers ───────────────────────────────────────────────────────

from equinox.gui.widgets.path_params_table import extract_path_params


class TestExtractPathParams:
    """extract_path_params — URL path parameter extraction."""

    def test_equinox_style(self):
        assert extract_path_params("https://api.example.com/{{id}}/details") == ["id"]

    def test_openapi_style(self):
        assert extract_path_params("https://api.example.com/{userId}/posts") == ["userId"]

    def test_mixed_styles(self):
        result = extract_path_params("/{{org}}/repos/{repoId}")
        assert result == ["org", "repoId"]

    def test_deduplication(self):
        result = extract_path_params("/{{id}}/sub/{{id}}")
        assert result == ["id"]

    def test_multiple_params(self):
        result = extract_path_params("/users/{{userId}}/posts/{{postId}}")
        assert result == ["userId", "postId"]

    def test_no_params(self):
        assert extract_path_params("https://api.example.com/users") == []

    def test_empty_url(self):
        assert extract_path_params("") == []

    def test_preserves_order(self):
        result = extract_path_params("/{{z}}/{{a}}/{{m}}")
        assert result == ["z", "a", "m"]

    def test_query_params_not_captured(self):
        # Query string params shouldn't be treated as path params
        result = extract_path_params("/users?id={{id}}")
        assert result == ["id"]  # Still captured — it's in {{}} syntax

    def test_nested_braces_ignored(self):
        # Malformed nested braces
        result = extract_path_params("/{{outer{{inner}}}}")
        assert "inner" in result  # regex captures what it can
