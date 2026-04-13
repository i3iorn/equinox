"""Tests for CookieJarManager."""

import pytest

from equinox.storage.database import Database
from equinox.storage.cookies import CookieJarManager
from equinox.core.exceptions import StorageError, ValidationError


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

    def test_different_paths_are_separate(self, mgr):
        mgr.add_cookie("c", "1", domain="x.com", path="/a")
        mgr.add_cookie("c", "2", domain="x.com", path="/b")
        assert len(mgr.list_cookies()) == 2

    def test_secure_flag(self, mgr):
        mgr.add_cookie("s", "1", domain="s.com", secure=True)
        c = mgr.list_cookies()[0]
        assert c["secure"] is True or c["secure"] == 1

    def test_http_only_flag(self, mgr):
        mgr.add_cookie("h", "1", domain="h.com", http_only=True)
        c = mgr.list_cookies()[0]
        assert c["http_only"] is True or c["http_only"] == 1

    def test_expires_stored(self, mgr):
        mgr.add_cookie("e", "1", domain="e.com", expires="2030-01-01")
        c = mgr.list_cookies()[0]
        assert c["expires"] == "2030-01-01"

    def test_delete_nonexistent_raises(self, mgr):
        with pytest.raises(StorageError, match="not found"):
            mgr.delete_cookie(999)


# ── update_cookie ────────────────────────────────────────────────────────────

class TestUpdateCookie:
    def test_update_value(self, mgr):
        cid = mgr.add_cookie("k", "old", domain="d.com")
        mgr.update_cookie(cid, value="new")
        assert mgr.get(cid)["value"] == "new"

    def test_update_secure(self, mgr):
        cid = mgr.add_cookie("k", "v", domain="d.com")
        mgr.update_cookie(cid, secure=True)
        assert mgr.get(cid)["secure"] is True

    def test_update_http_only(self, mgr):
        cid = mgr.add_cookie("k", "v", domain="d.com")
        mgr.update_cookie(cid, http_only=True)
        assert mgr.get(cid)["http_only"] is True

    def test_update_expires(self, mgr):
        cid = mgr.add_cookie("k", "v", domain="d.com")
        mgr.update_cookie(cid, expires="2030-12-31")
        assert mgr.get(cid)["expires"] == "2030-12-31"

    def test_update_multiple_fields(self, mgr):
        cid = mgr.add_cookie("k", "v", domain="d.com")
        mgr.update_cookie(cid, value="v2", secure=True, http_only=True)
        c = mgr.get(cid)
        assert c["value"] == "v2"
        assert c["secure"] is True
        assert c["http_only"] is True

    def test_update_no_fields_noop(self, mgr):
        cid = mgr.add_cookie("k", "v", domain="d.com")
        mgr.update_cookie(cid)
        assert mgr.get(cid)["value"] == "v"

    def test_update_nonexistent_raises(self, mgr):
        with pytest.raises(StorageError, match="not found"):
            mgr.update_cookie(999, value="x")


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

    def test_secure_and_httponly_flags(self, mgr):
        headers = {"Set-Cookie": "s=1; Secure; HttpOnly"}
        mgr.update_from_response(headers, "https://example.com")
        c = mgr.list_cookies()[0]
        assert c["secure"] is True
        assert c["http_only"] is True

    def test_expires_attribute(self, mgr):
        headers = {"Set-Cookie": "k=v; Expires=Thu, 01 Jan 2030 00:00:00 GMT"}
        mgr.update_from_response(headers, "https://example.com")
        c = mgr.list_cookies()[0]
        assert "2030" in c["expires"]

    def test_domain_dot_stripped(self, mgr):
        headers = {"Set-Cookie": "k=v; Domain=.sub.example.com"}
        mgr.update_from_response(headers, "https://sub.example.com")
        c = mgr.list_cookies()[0]
        assert c["domain"] == "sub.example.com"

    def test_cookie_name_only_no_equals(self, mgr):
        headers = {"Set-Cookie": "flag; Secure"}
        mgr.update_from_response(headers, "https://example.com")
        cookies = mgr.list_cookies()
        if cookies:
            assert cookies[0]["name"] == "flag"


# ── Validation ────────────────────────────────────────────────────────────────

class TestCookieValidation:
    def test_empty_name_raises(self, mgr):
        with pytest.raises(ValidationError, match="non-empty"):
            mgr.add_cookie("", "v")

    def test_whitespace_name_raises(self, mgr):
        with pytest.raises(ValidationError, match="non-empty"):
            mgr.add_cookie("   ", "v")

    def test_name_too_long(self, mgr):
        with pytest.raises(ValidationError):
            mgr.add_cookie("x" * 257, "v")

    def test_name_crlf(self, mgr):
        with pytest.raises(ValidationError, match="invalid"):
            mgr.add_cookie("bad\rname", "v")

    def test_value_too_long(self, mgr):
        with pytest.raises(ValidationError):
            mgr.add_cookie("k", "x" * 4097)

    def test_value_crlf(self, mgr):
        with pytest.raises(ValidationError, match="invalid"):
            mgr.add_cookie("k", "bad\nvalue")

    def test_domain_too_long(self, mgr):
        with pytest.raises(ValidationError):
            mgr.add_cookie("k", "v", domain="x" * 254)

    def test_domain_crlf(self, mgr):
        with pytest.raises(ValidationError, match="invalid"):
            mgr.add_cookie("k", "v", domain="bad\r\ndomain")

    def test_path_too_long(self, mgr):
        with pytest.raises(ValidationError):
            mgr.add_cookie("k", "v", path="/" + "x" * 1024)

    def test_path_crlf(self, mgr):
        with pytest.raises(ValidationError, match="invalid"):
            mgr.add_cookie("k", "v", path="/bad\npath")

    def test_expires_too_long(self, mgr):
        with pytest.raises(ValidationError, match="exceeds 100 characters"):
            mgr.add_cookie("k", "v", expires="x" * 101)

    def test_expires_crlf(self, mgr):
        with pytest.raises(ValidationError, match="invalid"):
            mgr.add_cookie("k", "v", expires="bad\nexpires")

    def test_non_string_value_raises(self, mgr):
        with pytest.raises(ValidationError):
            mgr.add_cookie("k", 123)

    def test_non_string_domain_raises(self, mgr):
        with pytest.raises(ValidationError):
            mgr.add_cookie("k", "v", domain=123)

    def test_non_string_path_raises(self, mgr):
        with pytest.raises(ValidationError):
            mgr.add_cookie("k", "v", path=123)

    def test_non_string_expires_raises(self, mgr):
        with pytest.raises(ValidationError):
            mgr.add_cookie("k", "v", expires=123)

    def test_max_cookies_limit(self, mgr):
        for i in range(CookieJarManager.MAX_COOKIES):
            mgr.add_cookie(f"c{i}", "v", domain=f"d{i}.com")
        with pytest.raises(StorageError, match="full"):
            mgr.add_cookie("overflow", "v", domain="new.com")
