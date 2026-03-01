"""Tests for CookieJarManager."""

import pytest

from equinox.storage.database import Database
from equinox.storage.cookies import CookieJarManager


@pytest.fixture
def db(tmp_path):
    return Database(str(tmp_path / "test.db"))


@pytest.fixture
def mgr(db):
    return CookieJarManager(db)


# ── CRUD ─────────────────────────────────────────────────────────────────────

class TestCookieCRUD:
    def test_add_and_list(self, mgr):
        mgr.add_cookie("session", "abc", domain="example.com")
        cookies = mgr.list_cookies()
        assert len(cookies) == 1
        assert cookies[0]["name"] == "session"
        assert cookies[0]["value"] == "abc"
        assert cookies[0]["domain"] == "example.com"

    def test_add_multiple(self, mgr):
        mgr.add_cookie("a", "1", domain="a.com")
        mgr.add_cookie("b", "2", domain="b.com")
        assert len(mgr.list_cookies()) == 2

    def test_delete(self, mgr):
        mgr.add_cookie("tok", "x", domain="x.com")
        cid = mgr.list_cookies()[0]["id"]
        mgr.delete_cookie(cid)
        assert mgr.list_cookies() == []

    def test_clear_all(self, mgr):
        mgr.add_cookie("a", "1")
        mgr.add_cookie("b", "2")
        mgr.clear_cookies()
        assert mgr.list_cookies() == []

    def test_get_by_id(self, mgr):
        mgr.add_cookie("x", "val", domain="d.com")
        cid = mgr.list_cookies()[0]["id"]
        c = mgr.get(cid)
        assert c is not None
        assert c["name"] == "x"

    def test_get_nonexistent_returns_none(self, mgr):
        assert mgr.get(9999) is None

    def test_upsert_on_duplicate_name_domain_path(self, mgr):
        mgr.add_cookie("tok", "v1", domain="d.com", path="/")
        mgr.add_cookie("tok", "v2", domain="d.com", path="/")
        cookies = mgr.list_cookies()
        assert len(cookies) == 1
        assert cookies[0]["value"] == "v2"

    def test_secure_flag(self, mgr):
        mgr.add_cookie("s", "1", domain="s.com", secure=True)
        c = mgr.list_cookies()[0]
        assert c["secure"] is True or c["secure"] == 1

    def test_http_only_flag(self, mgr):
        mgr.add_cookie("h", "1", domain="h.com", http_only=True)
        c = mgr.list_cookies()[0]
        assert c["http_only"] is True or c["http_only"] == 1


# ── to_httpx_cookies ─────────────────────────────────────────────────────────

class TestToHttpxCookies:
    def test_returns_name_value_dict(self, mgr):
        mgr.add_cookie("session", "s123", domain="api.com")
        mgr.add_cookie("csrf", "tok", domain="api.com")
        result = mgr.to_httpx_cookies()
        assert isinstance(result, dict)
        assert result["session"] == "s123"
        assert result["csrf"] == "tok"

    def test_empty_jar_returns_empty_dict(self, mgr):
        assert mgr.to_httpx_cookies() == {}


# ── update_from_response ─────────────────────────────────────────────────────

class TestUpdateFromResponse:
    def test_simple_set_cookie(self, mgr):
        headers = {"set-cookie": "session=abc; Path=/; Domain=example.com"}
        mgr.update_from_response(headers, "https://example.com/api")
        cookies = mgr.list_cookies()
        assert any(c["name"] == "session" and c["value"] == "abc" for c in cookies)

    def test_no_set_cookie_header_noop(self, mgr):
        mgr.update_from_response({"content-type": "application/json"}, "https://x.com")
        assert mgr.list_cookies() == []

    def test_updates_existing_cookie(self, mgr):
        mgr.add_cookie("tok", "old", domain="example.com", path="/")
        headers = {"set-cookie": "tok=new; Path=/; Domain=example.com"}
        mgr.update_from_response(headers, "https://example.com")
        cookies = mgr.list_cookies()
        assert len(cookies) == 1
        assert cookies[0]["value"] == "new"
