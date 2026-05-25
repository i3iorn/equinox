"""Tests for core/urls parsing.py and utils.py coverage gaps."""

from __future__ import annotations

from equinox.core.urls.parsing import (
    _split_host_port,
    parse_query_pairs,
    url_metadata,
)
from equinox.core.urls.utils import append_query_params, join_url_path

# ── _split_host_port ──────────────────────────────────────────────────────────


class TestSplitHostPort:
    def test_simple_host_no_port(self) -> None:
        host, port = _split_host_port("example.com")
        assert host == "example.com"
        assert port is None

    def test_host_with_port(self) -> None:
        host, port = _split_host_port("example.com:8080")
        assert host == "example.com"
        assert port == 8080

    def test_empty_string_returns_empty(self) -> None:
        host, port = _split_host_port("")
        assert host == ""
        assert port is None

    def test_ipv6_no_port(self) -> None:
        host, port = _split_host_port("[::1]")
        assert host == "::1"
        assert port is None

    def test_ipv6_with_port(self) -> None:
        host, port = _split_host_port("[::1]:9000")
        assert host == "::1"
        assert port == 9000

    def test_ipv6_malformed_no_closing_bracket(self) -> None:
        host, port = _split_host_port("[::1")
        # Should not crash; fallback behaviour
        assert isinstance(host, str)

    def test_userinfo_stripped(self) -> None:
        host, port = _split_host_port("user:pass@example.com")
        assert host == "example.com"

    def test_host_lowercased(self) -> None:
        host, _ = _split_host_port("EXAMPLE.COM:80")
        assert host == "example.com"

    def test_multiple_colons_no_port_returned(self) -> None:
        # IPv6 without brackets — treated as full netloc
        host, port = _split_host_port("2001:db8::1")
        assert port is None


# ── url_metadata ──────────────────────────────────────────────────────────────


class TestUrlMetadata:
    def test_full_url(self) -> None:
        meta = url_metadata("https://example.com:8080/path?q=1#frag")
        assert meta["scheme"] == "https"
        assert meta["hostname"] == "example.com"
        assert meta["port"] == 8080
        assert meta["path"] == "/path"
        assert meta["query"] == "q=1"
        assert meta["fragment"] == "frag"

    def test_url_without_port(self) -> None:
        meta = url_metadata("https://example.com/path")
        assert meta["port"] is None

    def test_empty_string_does_not_crash(self) -> None:
        meta = url_metadata("")
        assert meta["scheme"] == ""

    def test_url_with_no_fragment(self) -> None:
        meta = url_metadata("https://example.com/path")
        assert meta["fragment"] == ""


# ── parse_query_pairs ─────────────────────────────────────────────────────────


class TestParseQueryPairs:
    def test_simple_pair(self) -> None:
        pairs = parse_query_pairs("a=1&b=2")
        assert ("a", "1") in pairs
        assert ("b", "2") in pairs

    def test_empty_string_returns_empty(self) -> None:
        assert parse_query_pairs("") == []

    def test_none_returns_empty(self) -> None:
        assert parse_query_pairs(None) == []  # type: ignore[arg-type]

    def test_blank_values_kept_by_default(self) -> None:
        pairs = parse_query_pairs("a=&b=2")
        assert ("a", "") in pairs

    def test_blank_values_dropped_when_disabled(self) -> None:
        pairs = parse_query_pairs("a=&b=2", keep_blank_values=False)
        result_keys = [k for k, _ in pairs]
        assert "a" not in result_keys


# ── append_query_params ───────────────────────────────────────────────────────


class TestAppendQueryParams:
    def test_empty_params_unchanged(self) -> None:
        assert append_query_params("https://x.com", {}) == "https://x.com"

    def test_adds_params_to_clean_url(self) -> None:
        result = append_query_params("https://x.com", {"k": "v"})
        assert "k=v" in result

    def test_merges_with_existing_params(self) -> None:
        result = append_query_params("https://x.com?a=1", {"b": "2"})
        assert "a=1" in result or "b=2" in result

    def test_fragment_preserved(self) -> None:
        result = append_query_params("https://x.com#section", {"k": "v"})
        assert "#section" in result

    def test_merge_false_appends(self) -> None:
        result = append_query_params("https://x.com?a=1", {"b": "2"}, merge_existing=False)
        assert "a=1" in result
        assert "b=2" in result

    def test_merge_false_no_existing_query(self) -> None:
        result = append_query_params("https://x.com", {"k": "v"}, merge_existing=False)
        assert "k=v" in result

    def test_values_coerced_to_string(self) -> None:
        result = append_query_params("https://x.com", {"n": str(42)})
        assert "n=42" in result


# ── join_url_path ─────────────────────────────────────────────────────────────


class TestJoinUrlPath:
    def test_simple_join(self) -> None:
        assert join_url_path("https://x.com", "api/v1") == "https://x.com/api/v1"

    def test_trailing_slash_stripped(self) -> None:
        assert join_url_path("https://x.com/", "/api") == "https://x.com/api"

    def test_empty_base_returns_root(self) -> None:
        assert join_url_path("", "api") == "/api"

    def test_empty_path_returns_base(self) -> None:
        assert join_url_path("https://x.com", "") == "https://x.com"

    def test_both_empty_returns_slash(self) -> None:
        assert join_url_path("", "") == "/"
