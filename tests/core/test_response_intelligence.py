"""Tests for the Response Intelligence analysis engine."""

import json
import time

from equinox.core.request import Request, Response
from equinox.core.response_intelligence.analyzers.pii_secret_leak import (
    _SENSITIVE_VALUE_PATTERNS,
    _contains_sensitive_keys,
    _contains_sensitive_values,
)
from equinox.core.response_intelligence.consistency import (
    ContentTypeMismatchAnalyzer,
    DateFormatInconsistencyAnalyzer,
    DuplicateJsonKeysAnalyzer,
    NullVsMissingAnalyzer,
    RedirectLocationAnalyzer,
    SchemaDriftAnalyzer,
    StatusBodyMismatchAnalyzer,
)
from equinox.core.response_intelligence.engine import (
    AnalysisEngine,
    normalize_url_pattern,
)
from equinox.core.response_intelligence.hints import (
    DeprecatedAPIAnalyzer,
    LinkHeaderParsingAnalyzer,
    NPlusOneDetectionAnalyzer,
    ResponseEncodingIssuesAnalyzer,
    SuggestedEncodingAnalyzer,
)
from equinox.core.response_intelligence.models import (
    AnalysisContext,
    Category,
    Severity,
)
from equinox.core.response_intelligence.performance import (
    CompressionAnalyzer,
    PaginationDetectionAnalyzer,
    ResponseTimePercentileAnalyzer,
    TimingBreakdownAnalyzer,
)
from equinox.core.response_intelligence.security import (
    CookieFlagsAnalyzer,
    CORSMisconfigAnalyzer,
    JWTDecodeAnalyzer,
    MissingSecurityHeadersAnalyzer,
    PIILeakDetectionAnalyzer,
    SensitiveDataCachingAnalyzer,
)
from equinox.core.response_intelligence.server import (
    APIVersionDetectionAnalyzer,
    CachingBehaviorAnalyzer,
    RateLimitDashboardAnalyzer,
    ResponseTimeAnomalyAnalyzer,
    ServerFingerprintAnalyzer,
)

# ── Helpers ───────────────────────────────────────────────────────────


def _make_ctx(
    status=200,
    headers=None,
    body=b"",
    method="GET",
    url="https://api.example.com/v1/users",
    req_headers=None,
    timings=None,
    endpoint_stats=None,
    stored_schema=None,
    history_rows=None,
    elapsed=0.25,
):
    """Build a quick AnalysisContext for testing."""
    req = Request(method=method, url=url, headers=req_headers or {})
    hdrs = headers or {}
    # Normalise header keys to lowercase (Response.__post_init__ does this)
    hdrs = {k.lower(): v for k, v in hdrs.items()}
    if "content-type" not in hdrs and body:
        hdrs["content-type"] = "application/json"
    resp = Response(
        status_code=status,
        reason="OK",
        headers=hdrs,
        body=body,
        elapsed=elapsed,
        request=req,
        timings=timings,
    )
    return AnalysisContext(
        request=req,
        response=resp,
        history_rows=history_rows or [],
        endpoint_stats=endpoint_stats,
        stored_schema=stored_schema,
    )


# ── Engine tests ──────────────────────────────────────────────────────


class TestEngine:
    def test_discover_all_analyzers(self):
        analyzers = AnalysisEngine.discover_analyzers()
        assert len(analyzers) == 27
        ids = {a.analyzer_id for a in analyzers}
        assert "security.missing_headers" in ids
        assert "hints.link_header" in ids

    def test_disabled_analyzers_skipped(self):
        engine = AnalysisEngine(disabled={"security.missing_headers"})
        engine.load_analyzers()
        ids = {a.analyzer_id for a in engine._analyzers}
        assert "security.missing_headers" not in ids

    def test_findings_sorted_by_severity(self):
        ctx = _make_ctx(
            status=200,
            headers={"set-cookie": "foo=bar"},
            body=json.dumps({"error": "bad"}).encode(),
        )
        engine = AnalysisEngine()
        findings = engine.analyze(ctx)
        severities = [f.severity for f in findings]
        order = {Severity.CRITICAL: 0, Severity.WARNING: 1, Severity.INFO: 2}
        assert severities == sorted(severities, key=lambda s: order[s])

    def test_analyze_returns_list(self):
        ctx = _make_ctx(body=b'{"ok": true}')
        engine = AnalysisEngine()
        findings = engine.analyze(ctx)
        assert isinstance(findings, list)

    def test_get_all_analyzer_info(self):
        info = AnalysisEngine().get_all_analyzer_info()
        assert len(info) == 27
        assert all("id" in i and "name" in i and "category" in i for i in info)

    def test_get_all_analyzer_info_sorted_by_id(self):
        info = AnalysisEngine().get_all_analyzer_info()
        ids = [i["id"] for i in info]
        assert ids == sorted(ids)


class TestNormalizeUrl:
    def test_numeric_segments(self):
        assert (
            normalize_url_pattern("https://api.com/users/123/posts/456") == "/users/{id}/posts/{id}"
        )

    def test_uuid_segments(self):
        url = "https://api.com/items/550e8400-e29b-41d4-a716-446655440000/details"
        assert "{id}" in normalize_url_pattern(url)

    def test_strips_query(self):
        assert "?" not in normalize_url_pattern("https://api.com/users?page=2")


# ── Security tests ────────────────────────────────────────────────────


class TestMissingSecurityHeaders:
    def test_all_present(self):
        hdrs = {
            "strict-transport-security": "max-age=31536000",
            "content-security-policy": "default-src 'self'",
            "x-content-type-options": "nosniff",
            "x-frame-options": "DENY",
            "permissions-policy": "camera=()",
            "referrer-policy": "no-referrer",
        }
        ctx = _make_ctx(headers=hdrs, body=b'{"ok":true}')
        findings = MissingSecurityHeadersAnalyzer().analyze(ctx)
        assert len(findings) == 0

    def test_some_missing(self):
        ctx = _make_ctx(headers={}, body=b'{"ok":true}')
        findings = MissingSecurityHeadersAnalyzer().analyze(ctx)
        assert len(findings) == 1
        assert findings[0].severity in (Severity.WARNING, Severity.INFO)
        assert "header" in findings[0].title.lower()

    def test_invalid_header_values(self):
        hdrs = {
            "strict-transport-security": "max-age=10",
            "x-content-type-options": "invalid",
            "x-frame-options": "ALLOWALL",
        }
        ctx = _make_ctx(headers=hdrs, body=b'{"ok":true}')
        findings = MissingSecurityHeadersAnalyzer().analyze(ctx)
        assert len(findings) == 1
        assert findings[0].severity == Severity.WARNING
        assert findings[0].details["invalid"]


class TestCookieFlags:
    def test_secure_cookie_no_finding(self):
        hdrs = {"set-cookie": "id=abc; Secure; HttpOnly; SameSite=Strict"}
        ctx = _make_ctx(headers=hdrs, body=b"ok")
        findings = CookieFlagsAnalyzer().analyze(ctx)
        assert len(findings) == 0

    def test_insecure_cookie(self):
        hdrs = {"set-cookie": "id=abc; Path=/"}
        ctx = _make_ctx(headers=hdrs, body=b"ok")
        findings = CookieFlagsAnalyzer().analyze(ctx)
        assert len(findings) == 1
        assert findings[0].severity == Severity.WARNING

    def test_samesite_none_without_secure_is_critical(self):
        hdrs = {"set-cookie": "id=abc; HttpOnly; SameSite=None"}
        ctx = _make_ctx(headers=hdrs, body=b"ok")
        findings = CookieFlagsAnalyzer().analyze(ctx)
        assert len(findings) == 1
        assert findings[0].severity == Severity.CRITICAL

    def test_set_cookie_with_expires_and_second_cookie(self):
        hdrs = {
            "set-cookie": (
                "session=abc; Expires=Wed, 21 Oct 2026 07:28:00 GMT; Path=/; Secure; HttpOnly; SameSite=Lax, "
                "prefs=light; Path=/"
            ),
        }
        ctx = _make_ctx(headers=hdrs, body=b"ok")
        findings = CookieFlagsAnalyzer().analyze(ctx)
        assert len(findings) == 1
        assert any(issue["cookie"] == "prefs" for issue in findings[0].details["cookies"])


class TestPIILeak:
    def test_email_detected(self):
        body = json.dumps({"email": "user@example.com"}).encode()
        ctx = _make_ctx(body=body)
        findings = PIILeakDetectionAnalyzer().analyze(ctx)
        assert len(findings) == 1
        assert any(d["type"] == "Email address" for d in findings[0].details["detected"])

    def test_no_pii(self):
        body = json.dumps({"name": "Alice", "age": 30}).encode()
        ctx = _make_ctx(body=body)
        findings = PIILeakDetectionAnalyzer().analyze(ctx)
        assert len(findings) == 0

    def test_ssn_critical(self):
        body = json.dumps({"ssn": "123-45-6789"}).encode()
        ctx = _make_ctx(body=body)
        findings = PIILeakDetectionAnalyzer().analyze(ctx)
        assert len(findings) == 1
        assert findings[0].severity == Severity.CRITICAL

    def test_invalid_credit_card_number_not_flagged(self):
        body = json.dumps({"card": "1111111111111111"}).encode()
        ctx = _make_ctx(body=body)
        findings = PIILeakDetectionAnalyzer().analyze(ctx)
        assert len(findings) == 0

    def test_high_entropy_secret_detected(self):
        body = json.dumps({"note": "token AbCdefGHIjklMNOpQR123456789+/="}).encode()
        ctx = _make_ctx(body=body)
        findings = PIILeakDetectionAnalyzer().analyze(ctx)
        assert len(findings) == 1
        assert any(
            d["type"] == "High entropy secret-like token" for d in findings[0].details["detected"]
        )

    def test_sensitive_key_helpers(self):
        nested = {"level1": [{"meta": {"client_secret": "abc"}}]}
        assert _contains_sensitive_keys(nested, {"client_secret", "password"}) is True
        assert (
            _contains_sensitive_keys({"safe": [{"nested": 1}]}, {"client_secret", "password"})
            is False
        )

    def test_sensitive_value_helper_matches_patterns(self):
        token = "Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyIn0.signature"
        assert _contains_sensitive_values(token, _SENSITIVE_VALUE_PATTERNS) is True
        assert _contains_sensitive_values("plain text", _SENSITIVE_VALUE_PATTERNS) is False


class TestCORS:
    def test_wildcard_cors(self):
        hdrs = {"access-control-allow-origin": "*"}
        ctx = _make_ctx(headers=hdrs, body=b"ok")
        findings = CORSMisconfigAnalyzer().analyze(ctx)
        assert len(findings) == 1

    def test_no_cors_header(self):
        ctx = _make_ctx(headers={}, body=b"ok")
        findings = CORSMisconfigAnalyzer().analyze(ctx)
        assert len(findings) == 0

    def test_wildcard_with_credentials_is_critical(self):
        hdrs = {
            "access-control-allow-origin": "*",
            "access-control-allow-credentials": "true",
        }
        ctx = _make_ctx(headers=hdrs, body=b"ok")
        findings = CORSMisconfigAnalyzer().analyze(ctx)
        assert len(findings) == 1
        assert findings[0].severity == Severity.CRITICAL

    def test_reflected_origin_with_credentials_requires_vary(self):
        hdrs = {
            "access-control-allow-origin": "https://app.example.com",
            "access-control-allow-credentials": "true",
        }
        ctx = _make_ctx(
            headers=hdrs,
            req_headers={"Origin": "https://app.example.com"},
            body=b"ok",
        )
        findings = CORSMisconfigAnalyzer().analyze(ctx)
        assert len(findings) == 1
        assert "vary" in findings[0].description.lower()


class TestJWTDecode:
    def _make_jwt(self, claims: dict, exp: int = None) -> str:
        import base64

        header = (
            base64.urlsafe_b64encode(json.dumps({"alg": "HS256"}).encode()).rstrip(b"=").decode()
        )
        if exp is not None:
            claims["exp"] = exp
        payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
        sig = base64.urlsafe_b64encode(b"fakesig").rstrip(b"=").decode()
        return f"{header}.{payload}.{sig}"

    def test_jwt_in_body(self):
        jwt = self._make_jwt({"sub": "user1", "iss": "auth.example.com"})
        body = json.dumps({"access_token": jwt}).encode()
        ctx = _make_ctx(body=body)
        findings = JWTDecodeAnalyzer().analyze(ctx)
        assert len(findings) >= 1
        assert findings[0].category == Category.SECURITY

    def test_expired_jwt(self):
        jwt = self._make_jwt({"sub": "user1"}, exp=int(time.time()) - 3600)
        body = json.dumps({"token": jwt}).encode()
        ctx = _make_ctx(body=body)
        findings = JWTDecodeAnalyzer().analyze(ctx)
        assert any(f.severity == Severity.WARNING for f in findings)

    def test_alg_none_is_critical(self):
        import base64

        header = (
            base64.urlsafe_b64encode(json.dumps({"alg": "none"}).encode()).rstrip(b"=").decode()
        )
        payload = (
            base64.urlsafe_b64encode(
                json.dumps({"sub": "u1", "exp": int(time.time()) + 600}).encode(),
            )
            .rstrip(b"=")
            .decode()
        )
        token = f"{header}.{payload}.fakesig"
        body = json.dumps({"access_token": token}).encode()
        ctx = _make_ctx(body=body)
        findings = JWTDecodeAnalyzer().analyze(ctx)
        assert any(f.severity == Severity.CRITICAL for f in findings)

    def test_claims_are_redacted(self):
        jwt = self._make_jwt(
            {"sub": "user1", "iss": "auth.example.com", "email": "user@example.com"},
        )
        body = json.dumps({"access_token": jwt}).encode()
        ctx = _make_ctx(body=body)
        findings = JWTDecodeAnalyzer().analyze(ctx)
        assert len(findings) >= 1
        details = findings[0].details
        assert "claims" in details
        assert "sub" not in details["claims"]
        assert "email" not in details["claims"]
        assert details["claims"].get("iss") == "auth.example.com"

    def test_jwt_in_authorization_header(self):
        jwt = self._make_jwt(
            {"sub": "user1", "iss": "auth.example.com"},
            exp=int(time.time()) + 600,
        )
        ctx = _make_ctx(headers={"authorization": f"Bearer {jwt}"}, body=b"")
        findings = JWTDecodeAnalyzer().analyze(ctx)
        assert len(findings) == 1
        assert findings[0].details["source"] == "header"

    def test_long_lived_and_missing_exp_branches(self):
        long_lived = self._make_jwt({"sub": "user1"}, exp=int(time.time()) + (60 * 60 * 24 * 31))
        missing_exp = self._make_jwt({"sub": "user2"})

        long_ctx = _make_ctx(body=json.dumps({"access_token": long_lived}).encode())
        missing_ctx = _make_ctx(body=json.dumps({"access_token": missing_exp}).encode())

        long_findings = JWTDecodeAnalyzer().analyze(long_ctx)
        missing_findings = JWTDecodeAnalyzer().analyze(missing_ctx)

        assert long_findings[0].details.get("long_lived") is True
        assert missing_findings[0].details.get("missing_exp") is True

    def test_sanitize_claims_keeps_safe_list_values(self):
        sanitized = JWTDecodeAnalyzer._sanitize_claims(
            {"aud": [1, "two", {"three": 3}], "scope": ["read", "write"], "email": "x@example.com"},
        )

        assert sanitized["aud"] == ["1", "two", "{'three': 3}"]
        assert sanitized["scope"] == ["read", "write"]
        assert "email" not in sanitized


class TestSensitiveDataCaching:
    def test_sensitive_body_without_no_store(self):
        body = json.dumps({"access_token": "abc", "user": "alice"}).encode()
        hdrs = {"cache-control": "private, max-age=600"}
        ctx = _make_ctx(headers=hdrs, body=body)
        findings = SensitiveDataCachingAnalyzer().analyze(ctx)
        assert len(findings) == 1
        assert findings[0].severity == Severity.WARNING

    def test_sensitive_body_with_public_cache_is_critical(self):
        body = json.dumps({"refresh_token": "abc"}).encode()
        hdrs = {"cache-control": "public, max-age=3600"}
        ctx = _make_ctx(headers=hdrs, body=body)
        findings = SensitiveDataCachingAnalyzer().analyze(ctx)
        assert len(findings) == 1
        assert findings[0].severity == Severity.CRITICAL

    def test_sensitive_body_with_no_store_no_finding(self):
        body = json.dumps({"access_token": "abc"}).encode()
        hdrs = {"cache-control": "no-store"}
        ctx = _make_ctx(headers=hdrs, body=body)
        findings = SensitiveDataCachingAnalyzer().analyze(ctx)
        assert len(findings) == 0

    def test_sensitive_value_pattern_detected_without_sensitive_key(self):
        body = json.dumps({"data": "Bearer abc.def.ghi"}).encode()
        hdrs = {"cache-control": "private, max-age=600"}
        ctx = _make_ctx(headers=hdrs, body=body)
        findings = SensitiveDataCachingAnalyzer().analyze(ctx)
        assert len(findings) == 1
        assert "sensitive value patterns in body" in findings[0].details["signals"]


# ── Performance tests ─────────────────────────────────────────────────


class TestCompression:
    def test_compressed_response(self):
        hdrs = {"content-encoding": "gzip", "content-type": "application/json"}
        ctx = _make_ctx(headers=hdrs, body=b'{"ok":true}')
        findings = CompressionAnalyzer().analyze(ctx)
        assert len(findings) == 1
        assert findings[0].severity == Severity.INFO
        assert "gzip" in findings[0].title.lower()

    def test_large_uncompressed(self):
        body = b"x" * 20_000
        hdrs = {"content-type": "application/json"}
        ctx = _make_ctx(headers=hdrs, body=body)
        findings = CompressionAnalyzer().analyze(ctx)
        assert len(findings) == 1
        assert "not compressed" in findings[0].title.lower()


class TestTimingBreakdown:
    def test_with_timings(self):
        timings = {
            "total_ms": 500,
            "dns_ms": 50,
            "connect_ms": 100,
            "tls_ms": 80,
            "ttfb_ms": 200,
            "transfer_ms": 70,
        }
        ctx = _make_ctx(timings=timings, body=b"{}")
        findings = TimingBreakdownAnalyzer().analyze(ctx)
        assert len(findings) == 1
        assert "500 ms" in findings[0].title

    def test_no_timings(self):
        ctx = _make_ctx(body=b"{}")
        findings = TimingBreakdownAnalyzer().analyze(ctx)
        assert len(findings) == 0


class TestPercentiles:
    def test_with_stats(self):
        stats = {
            "elapsed_values": json.dumps([100, 120, 110, 130, 115, 105, 125, 140, 108, 112]),
            "call_count": 10,
        }
        ctx = _make_ctx(endpoint_stats=stats, elapsed=0.115, body=b"{}")
        findings = ResponseTimePercentileAnalyzer().analyze(ctx)
        assert len(findings) == 1
        assert "P50" in findings[0].title

    def test_insufficient_data(self):
        stats = {"elapsed_values": json.dumps([100]), "call_count": 1}
        ctx = _make_ctx(endpoint_stats=stats, body=b"{}")
        findings = ResponseTimePercentileAnalyzer().analyze(ctx)
        assert len(findings) == 0

    def test_ignores_malformed_sample_values(self):
        stats = {
            "elapsed_values": json.dumps([100, "bad", None, "101", float("inf")]),
            "call_count": 5,
        }
        ctx = _make_ctx(endpoint_stats=stats, body=b"{}")
        findings = ResponseTimePercentileAnalyzer().analyze(ctx)
        assert len(findings) == 0


class TestPagination:
    def test_paginated_response(self):
        body = json.dumps(
            {
                "data": [{"id": 1}],
                "page": 2,
                "total_pages": 10,
                "total": 95,
            },
        ).encode()
        ctx = _make_ctx(body=body)
        findings = PaginationDetectionAnalyzer().analyze(ctx)
        assert len(findings) == 1
        assert "Page 2 of 10" in findings[0].description

    def test_non_paginated(self):
        body = json.dumps({"name": "Alice"}).encode()
        ctx = _make_ctx(body=body)
        findings = PaginationDetectionAnalyzer().analyze(ctx)
        assert len(findings) == 0


# ── Consistency tests ─────────────────────────────────────────────────


class TestStatusBodyMismatch:
    def test_204_with_body(self):
        ctx = _make_ctx(status=204, body=b'{"extra": "data"}')
        findings = StatusBodyMismatchAnalyzer().analyze(ctx)
        assert len(findings) == 1
        assert "204" in findings[0].title

    def test_200_with_error_key(self):
        body = json.dumps({"error": "something went wrong"}).encode()
        ctx = _make_ctx(status=200, body=body)
        findings = StatusBodyMismatchAnalyzer().analyze(ctx)
        assert len(findings) == 1
        assert "error" in findings[0].title.lower()

    def test_200_with_nested_error_key(self):
        body = json.dumps({"meta": {"errorMessage": "upstream failure"}}).encode()
        ctx = _make_ctx(status=200, body=body)
        findings = StatusBodyMismatchAnalyzer().analyze(ctx)
        assert len(findings) == 1
        assert "meta.errorMessage" in findings[0].details.get("error_paths", [])

    def test_304_with_body(self):
        ctx = _make_ctx(status=304, body=b"stale")
        findings = StatusBodyMismatchAnalyzer().analyze(ctx)
        assert len(findings) == 1
        assert "304" in findings[0].title

    def test_200_clean(self):
        body = json.dumps({"data": [1, 2, 3]}).encode()
        ctx = _make_ctx(status=200, body=body)
        findings = StatusBodyMismatchAnalyzer().analyze(ctx)
        assert len(findings) == 0


class TestContentTypeMismatch:
    def test_json_header_invalid_body(self):
        hdrs = {"content-type": "application/json"}
        ctx = _make_ctx(headers=hdrs, body=b"this is not json")
        findings = ContentTypeMismatchAnalyzer().analyze(ctx)
        assert len(findings) == 1
        assert "JSON" in findings[0].title

    def test_json_header_valid_body(self):
        hdrs = {"content-type": "application/json"}
        ctx = _make_ctx(headers=hdrs, body=b'{"ok": true}')
        findings = ContentTypeMismatchAnalyzer().analyze(ctx)
        assert len(findings) == 0

    def test_large_valid_json_not_flagged_by_truncation(self):
        payload = {"items": [{"id": i, "name": f"user-{i}"} for i in range(700)]}
        body = json.dumps(payload).encode()
        hdrs = {"content-type": "application/json"}
        ctx = _make_ctx(headers=hdrs, body=body)
        findings = ContentTypeMismatchAnalyzer().analyze(ctx)
        assert len(findings) == 0

    def test_huge_json_with_non_json_prefix_warns(self):
        hdrs = {"content-type": "application/json"}
        body = (b"x" * 10) + (b" " * 10) + (b"y" * (1_000_100))
        ctx = _make_ctx(headers=hdrs, body=body)
        findings = ContentTypeMismatchAnalyzer().analyze(ctx)
        assert len(findings) == 1
        assert findings[0].severity == Severity.WARNING


class TestDuplicateJsonKeys:
    def test_duplicate_keys(self):
        raw = b'{"key": 1, "key": 2}'
        hdrs = {"content-type": "application/json"}
        ctx = _make_ctx(headers=hdrs, body=raw)
        findings = DuplicateJsonKeysAnalyzer().analyze(ctx)
        assert len(findings) == 1
        assert "key" in findings[0].details["duplicate_keys"]

    def test_no_duplicates(self):
        body = json.dumps({"a": 1, "b": 2}).encode()
        ctx = _make_ctx(body=body)
        findings = DuplicateJsonKeysAnalyzer().analyze(ctx)
        assert len(findings) == 0

    def test_large_body_reports_scan_skipped(self):
        raw = b"{" + (b'"a":1,' * 200_000) + b'"z":2}'
        hdrs = {"content-type": "application/json"}
        ctx = _make_ctx(headers=hdrs, body=raw)
        findings = DuplicateJsonKeysAnalyzer().analyze(ctx)
        assert len(findings) == 1
        assert "skipped" in findings[0].title.lower()


class TestDateFormats:
    def test_mixed_formats(self):
        body = json.dumps(
            {
                "created": "2025-01-15T10:30:00Z",
                "updated": 1705312200,
                "display_date": "01/15/2025",
            },
        ).encode()
        ctx = _make_ctx(body=body)
        findings = DateFormatInconsistencyAnalyzer().analyze(ctx)
        assert len(findings) == 1
        assert "Mixed" in findings[0].title

    def test_consistent_format(self):
        body = json.dumps(
            {
                "created": "2025-01-15T10:30:00Z",
                "updated": "2025-02-20T14:00:00Z",
            },
        ).encode()
        ctx = _make_ctx(body=body)
        findings = DateFormatInconsistencyAnalyzer().analyze(ctx)
        # Only ISO 8601 found — no inconsistency
        assert len(findings) == 0


class TestNullVsMissing:
    def test_mixed_patterns(self):
        body = json.dumps(
            [
                {"name": "Alice", "email": None},
                {"name": "Bob"},  # email key missing
            ],
        ).encode()
        ctx = _make_ctx(body=body)
        findings = NullVsMissingAnalyzer().analyze(ctx)
        assert len(findings) == 1
        assert "email" in findings[0].details["fields"]

    def test_consistent(self):
        body = json.dumps(
            [
                {"name": "Alice", "email": None},
                {"name": "Bob", "email": None},
            ],
        ).encode()
        ctx = _make_ctx(body=body)
        findings = NullVsMissingAnalyzer().analyze(ctx)
        assert len(findings) == 0


class TestSchemaDrift:
    def test_field_added(self):
        stored = {"name": "str", "age": "int"}
        body = json.dumps({"name": "Alice", "age": 30, "role": "admin"}).encode()
        ctx = _make_ctx(body=body, stored_schema=stored)
        findings = SchemaDriftAnalyzer().analyze(ctx)
        assert len(findings) == 1
        assert "role" in findings[0].details["added"]

    def test_field_removed(self):
        stored = {"name": "str", "age": "int", "email": "str"}
        body = json.dumps({"name": "Alice", "age": 30}).encode()
        ctx = _make_ctx(body=body, stored_schema=stored)
        findings = SchemaDriftAnalyzer().analyze(ctx)
        assert len(findings) == 1
        assert "email" in findings[0].details["removed"]

    def test_no_drift(self):
        stored = {"name": "str", "age": "int"}
        body = json.dumps({"name": "Alice", "age": 30}).encode()
        ctx = _make_ctx(body=body, stored_schema=stored)
        findings = SchemaDriftAnalyzer().analyze(ctx)
        assert len(findings) == 0

    def test_no_stored_schema(self):
        body = json.dumps({"name": "Alice"}).encode()
        ctx = _make_ctx(body=body, stored_schema=None)
        findings = SchemaDriftAnalyzer().analyze(ctx)
        assert len(findings) == 0

    def test_list_schema_drift_detects_fields_beyond_first_item(self):
        stored = {"[]": "dict", "[].id": "int"}
        body = json.dumps([{"id": 1}, {"id": 2, "email": "a@example.com"}]).encode()
        ctx = _make_ctx(body=body, stored_schema=stored)
        findings = SchemaDriftAnalyzer().analyze(ctx)
        assert len(findings) == 1
        assert "[].email" in findings[0].details["added"]


class TestRedirectLocation:
    def test_redirect_without_location(self):
        ctx = _make_ctx(status=302, headers={}, body=b"")
        findings = RedirectLocationAnalyzer().analyze(ctx)
        assert len(findings) == 1
        assert "location" in findings[0].title.lower()

    def test_redirect_with_location(self):
        ctx = _make_ctx(status=307, headers={"location": "https://api.example.com/new"}, body=b"")
        findings = RedirectLocationAnalyzer().analyze(ctx)
        assert len(findings) == 0


# ── Server tests ──────────────────────────────────────────────────────


class TestServerFingerprint:
    def test_detected(self):
        hdrs = {"server": "nginx/1.25.3", "x-powered-by": "Express"}
        ctx = _make_ctx(headers=hdrs, body=b"{}")
        findings = ServerFingerprintAnalyzer().analyze(ctx)
        assert len(findings) == 1
        assert "nginx" in findings[0].title

    def test_no_server_header(self):
        ctx = _make_ctx(headers={}, body=b"{}")
        findings = ServerFingerprintAnalyzer().analyze(ctx)
        assert len(findings) == 0


class TestRateLimit:
    def test_rate_limit_headers(self):
        hdrs = {
            "x-ratelimit-limit": "100",
            "x-ratelimit-remaining": "42",
        }
        ctx = _make_ctx(headers=hdrs, body=b"{}")
        findings = RateLimitDashboardAnalyzer().analyze(ctx)
        assert len(findings) == 1
        assert "Limit: 100" in findings[0].description

    def test_429_status(self):
        hdrs = {"retry-after": "30"}
        ctx = _make_ctx(status=429, headers=hdrs, body=b"{}")
        findings = RateLimitDashboardAnalyzer().analyze(ctx)
        assert len(findings) == 1
        assert findings[0].severity == Severity.CRITICAL

    def test_reset_epoch_milliseconds(self):
        future_ms = int((time.time() + 60) * 1000)
        hdrs = {
            "x-ratelimit-limit": "100",
            "x-ratelimit-remaining": "10",
            "x-ratelimit-reset": str(future_ms),
        }
        ctx = _make_ctx(headers=hdrs, body=b"{}")
        findings = RateLimitDashboardAnalyzer().analyze(ctx)
        assert len(findings) == 1
        secs = findings[0].details.get("resets_in_seconds")
        assert isinstance(secs, int)
        assert 1 <= secs <= 120


class TestCaching:
    def test_cache_control(self):
        hdrs = {"cache-control": "public, max-age=3600"}
        ctx = _make_ctx(headers=hdrs, body=b"{}")
        findings = CachingBehaviorAnalyzer().analyze(ctx)
        assert len(findings) == 1
        assert "cached" in findings[0].title.lower()

    def test_no_caching_headers(self):
        ctx = _make_ctx(headers={}, body=b"{}")
        findings = CachingBehaviorAnalyzer().analyze(ctx)
        assert len(findings) == 0


class TestAPIVersion:
    def test_version_in_url(self):
        ctx = _make_ctx(url="https://api.example.com/v2/users", body=b"{}")
        findings = APIVersionDetectionAnalyzer().analyze(ctx)
        assert len(findings) == 1
        assert "v2" in findings[0].title

    def test_version_in_header(self):
        hdrs = {"api-version": "2024-01-01"}
        ctx = _make_ctx(headers=hdrs, url="https://api.example.com/users", body=b"{}")
        findings = APIVersionDetectionAnalyzer().analyze(ctx)
        assert len(findings) == 1


class TestResponseTimeAnomaly:
    def test_anomalous_slow(self):
        values = [100, 105, 98, 110, 102, 99, 103, 107, 101, 104]
        stats = {"elapsed_values": json.dumps(values), "call_count": 10}
        ctx = _make_ctx(endpoint_stats=stats, elapsed=0.5, body=b"{}")  # 500ms vs ~103ms avg
        findings = ResponseTimeAnomalyAnalyzer().analyze(ctx)
        assert len(findings) == 1
        assert findings[0].severity == Severity.WARNING

    def test_normal_response(self):
        values = [100, 105, 98, 110, 102, 99, 103, 107, 101, 104]
        stats = {"elapsed_values": json.dumps(values), "call_count": 10}
        ctx = _make_ctx(endpoint_stats=stats, elapsed=0.103, body=b"{}")
        findings = ResponseTimeAnomalyAnalyzer().analyze(ctx)
        assert len(findings) == 0

    def test_malformed_samples_no_crash(self):
        stats = {
            "elapsed_values": json.dumps(["bad", None, "oops", {}]),
            "call_count": 4,
        }
        ctx = _make_ctx(endpoint_stats=stats, elapsed=0.2, body=b"{}")
        findings = ResponseTimeAnomalyAnalyzer().analyze(ctx)
        assert findings == []


# ── Hints tests ───────────────────────────────────────────────────────


class TestDeprecated:
    def test_sunset_header(self):
        hdrs = {"sunset": "Sat, 01 Mar 2025 00:00:00 GMT"}
        ctx = _make_ctx(headers=hdrs, body=b"{}")
        findings = DeprecatedAPIAnalyzer().analyze(ctx)
        assert len(findings) == 1
        assert findings[0].severity == Severity.CRITICAL

    def test_deprecation_header(self):
        hdrs = {"deprecation": "true"}
        ctx = _make_ctx(headers=hdrs, body=b"{}")
        findings = DeprecatedAPIAnalyzer().analyze(ctx)
        assert len(findings) == 1

    def test_no_deprecation(self):
        ctx = _make_ctx(headers={}, body=b"{}")
        findings = DeprecatedAPIAnalyzer().analyze(ctx)
        assert len(findings) == 0


class TestSuggestedEncoding:
    def test_large_uncompressed_no_ae(self):
        body = b"x" * 2000
        hdrs = {"content-type": "application/json"}
        ctx = _make_ctx(headers=hdrs, body=body, req_headers={})
        findings = SuggestedEncodingAnalyzer().analyze(ctx)
        assert len(findings) == 1

    def test_ae_already_sent(self):
        body = b"x" * 2000
        hdrs = {"content-type": "application/json"}
        ctx = _make_ctx(headers=hdrs, body=body, req_headers={"Accept-Encoding": "gzip"})
        findings = SuggestedEncodingAnalyzer().analyze(ctx)
        assert len(findings) == 0


class TestNPlusOne:
    def test_sequential_similar_requests(self):
        history = [
            {"method": "GET", "url": f"https://api.com/users/{i}", "elapsed": 0.1}
            for i in range(10)
        ]
        ctx = _make_ctx(body=b"{}", history_rows=history)
        findings = NPlusOneDetectionAnalyzer().analyze(ctx)
        assert len(findings) == 1
        assert "N+1" in findings[0].title

    def test_varied_requests(self):
        history = [
            {"method": "GET", "url": "https://api.com/users", "elapsed": 0.1},
            {"method": "POST", "url": "https://api.com/orders", "elapsed": 0.2},
            {"method": "GET", "url": "https://api.com/products", "elapsed": 0.15},
        ]
        ctx = _make_ctx(body=b"{}", history_rows=history)
        findings = NPlusOneDetectionAnalyzer().analyze(ctx)
        assert len(findings) == 0

    def test_groups_same_pattern_with_different_n(self):
        history = [
            {"method": "GET", "url": "https://api.com/users/1"},
            {"method": "GET", "url": "https://api.com/users/2"},
            {"method": "GET", "url": "https://api.com/users/3"},
            {"method": "GET", "url": "https://api.com/users/4"},
            {"method": "POST", "url": "https://api.com/orders"},
            {"method": "GET", "url": "https://api.com/users/10"},
            {"method": "GET", "url": "https://api.com/users/11"},
            {"method": "GET", "url": "https://api.com/users/12"},
            {"method": "GET", "url": "https://api.com/users/13"},
            {"method": "GET", "url": "https://api.com/users/14"},
        ]
        ctx = _make_ctx(body=b"{}", history_rows=history)
        findings = NPlusOneDetectionAnalyzer().analyze(ctx)
        assert len(findings) == 1
        detail = findings[0].details
        assert detail["counts"] == [4, 5]
        assert detail["n_min"] == 4
        assert detail["n_max"] == 5

    def test_interleaved_pattern_is_detected(self):
        history = []
        for i in range(6):
            history.append({"method": "GET", "url": f"https://api.com/users/{i}"})
            history.append({"method": "GET", "url": f"https://api.com/orders/{i}"})
        ctx = _make_ctx(body=b"{}", history_rows=history)
        findings = NPlusOneDetectionAnalyzer().analyze(ctx)
        assert any("GET /users/{id}" in finding.details["pattern"] for finding in findings)


class TestEncodingIssues:
    def test_bom_detected(self):
        body = b"\xef\xbb\xbf" + b'{"ok": true}'
        ctx = _make_ctx(body=body)
        findings = ResponseEncodingIssuesAnalyzer().analyze(ctx)
        assert len(findings) == 1
        assert findings[0].details.get("bom") is True

    def test_clean_response(self):
        body = json.dumps({"ok": True}).encode()
        ctx = _make_ctx(body=body)
        findings = ResponseEncodingIssuesAnalyzer().analyze(ctx)
        assert len(findings) == 0


class TestLinkHeader:
    def test_link_with_next(self):
        hdrs = {
            "link": '<https://api.com/users?page=3>; rel="next", <https://api.com/users?page=1>; rel="prev"',
        }
        ctx = _make_ctx(headers=hdrs, body=b"{}")
        findings = LinkHeaderParsingAnalyzer().analyze(ctx)
        assert len(findings) == 1
        assert len(findings[0].details["links"]) == 2

    def test_no_link_header(self):
        ctx = _make_ctx(headers={}, body=b"{}")
        findings = LinkHeaderParsingAnalyzer().analyze(ctx)
        assert len(findings) == 0


# ── Storage migration test ────────────────────────────────────────────


class TestIntelligenceMigration:
    """Test that migration v20 creates the expected tables."""

    def test_tables_created(self, tmp_path):
        from equinox.storage.database import Database

        with Database(str(tmp_path / "test.db")) as db:
            # Tables should exist after Database.__init__ runs migrations
            rows = db.fetchall(
                "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('endpoint_stats', 'response_schemas')",
            )
            names = {r["name"] for r in rows}
            assert "endpoint_stats" in names
            assert "response_schemas" in names


# ── Storage manager test ──────────────────────────────────────────────


class TestResponseIntelligenceManager:
    def test_endpoint_stats_roundtrip(self, tmp_path):
        from equinox.storage.database import Database
        from equinox.storage.response_intelligence import ResponseIntelligenceManager

        with Database(str(tmp_path / "test.db")) as db:
            mgr = ResponseIntelligenceManager(db)

            # Initially empty
            assert mgr.get_endpoint_stats("/users/{id}", "GET") is None

            # Insert
            mgr.update_endpoint_stats("/users/{id}", "GET", 150.0)
            stats = mgr.get_endpoint_stats("/users/{id}", "GET")
            assert stats is not None
            assert stats["call_count"] == 1

            # Update
            mgr.update_endpoint_stats("/users/{id}", "GET", 200.0)
            stats = mgr.get_endpoint_stats("/users/{id}", "GET")
            assert stats["call_count"] == 2
            values = json.loads(stats["elapsed_values"])
            assert len(values) == 2

    def test_schema_roundtrip(self, tmp_path):
        from equinox.storage.database import Database
        from equinox.storage.response_intelligence import ResponseIntelligenceManager

        with Database(str(tmp_path / "test.db")) as db:
            mgr = ResponseIntelligenceManager(db)

            schema = {"name": "str", "age": "int"}
            mgr.save_schema("/users/{id}", "GET", schema, status_code=200)
            loaded = mgr.get_schema("/users/{id}", "GET", status_code=200)
            assert loaded == schema

            # Update
            new_schema = {"name": "str", "age": "int", "role": "str"}
            mgr.save_schema("/users/{id}", "GET", new_schema, status_code=200)
            loaded = mgr.get_schema("/users/{id}", "GET", status_code=200)
            assert loaded == new_schema

    def test_schema_isolated_by_status_code(self, tmp_path):
        from equinox.storage.database import Database
        from equinox.storage.response_intelligence import ResponseIntelligenceManager

        with Database(str(tmp_path / "test.db")) as db:
            mgr = ResponseIntelligenceManager(db)

            ok_schema = {"id": "int", "name": "str"}
            not_found_schema = {"error": "str", "code": "int"}
            mgr.save_schema("/users/{id}", "GET", ok_schema, status_code=200)
            mgr.save_schema("/users/{id}", "GET", not_found_schema, status_code=404)

            assert mgr.get_schema("/users/{id}", "GET", status_code=200) == ok_schema
            assert mgr.get_schema("/users/{id}", "GET", status_code=404) == not_found_schema
            assert mgr.get_schema("/users/{id}", "GET", status_code=500) is None

    def test_legacy_schema_does_not_cross_match_status(self, tmp_path):
        from equinox.storage.database import Database
        from equinox.storage.response_intelligence import ResponseIntelligenceManager

        with Database(str(tmp_path / "test.db")) as db:
            mgr = ResponseIntelligenceManager(db)

            legacy_schema = {"legacy": "str"}
            mgr.save_schema("/users/{id}", "GET", legacy_schema)

            assert mgr.get_schema("/users/{id}", "GET") == legacy_schema
            assert mgr.get_schema("/users/{id}", "GET", status_code=200) is None

    def test_recent_history(self, tmp_path):
        from equinox.storage.database import Database
        from equinox.storage.response_intelligence import ResponseIntelligenceManager

        with Database(str(tmp_path / "test.db")) as db:
            mgr = ResponseIntelligenceManager(db)
            rows = mgr.get_recent_history(limit=10)
            assert isinstance(rows, list)
