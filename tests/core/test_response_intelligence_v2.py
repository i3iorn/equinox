"""Smoke and regression tests for response_intelligence"""

import json
import time

from equinox.core.request import Request, Response
from equinox.core.response_intelligence import AnalysisEngine, normalize_url_pattern
from equinox.core.response_intelligence import AnalysisContext, Category, Severity
from equinox.core.response_intelligence.security import (
    CORSMisconfigAnalyzer,
    JWTDecodeAnalyzer,
    MissingSecurityHeadersAnalyzer,
)
from equinox.core.response_intelligence.performance import (
    CompressionAnalyzer,
    PaginationDetectionAnalyzer,
)
from equinox.core.response_intelligence.consistency import (
    RedirectLocationAnalyzer,
    SchemaDriftAnalyzer,
)
from equinox.core.response_intelligence.server import ResponseTimeAnomalyAnalyzer
from equinox.core.response_intelligence.hints import NPlusOneDetectionAnalyzer


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
    req = Request(method=method, url=url, headers=req_headers or {})
    hdrs = {k.lower(): v for k, v in (headers or {}).items()}
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


class TestV2Engine:
    def test_discover_all_analyzers(self):
        analyzers = AnalysisEngine.discover_analyzers()
        assert len(analyzers) == 27
        ids = {a.analyzer_id for a in analyzers}
        assert "security.missing_headers" in ids
        assert "consistency.redirect_location" in ids

    def test_disabled_analyzer_skipped(self):
        engine = AnalysisEngine(disabled={"security.missing_headers"})
        engine.load_analyzers()
        ids = {a.analyzer_id for a in engine._analyzers}
        assert "security.missing_headers" not in ids

    def test_findings_sorted_by_severity(self):
        ctx = _make_ctx(
            headers={
                "set-cookie": "id=abc; HttpOnly; SameSite=None",
                "access-control-allow-origin": "*",
                "access-control-allow-credentials": "true",
            },
            body=json.dumps({"ok": True}).encode(),
        )
        findings = AnalysisEngine().analyze(ctx)
        severities = [f.severity for f in findings]
        order = {Severity.CRITICAL: 0, Severity.WARNING: 1, Severity.INFO: 2}
        assert severities == sorted(severities, key=lambda s: order[s])


class TestV2Helpers:
    def test_normalize_url_pattern(self):
        assert normalize_url_pattern("https://api.com/users/123/posts/456") == "/users/{id}/posts/{id}"


class TestV2Security:
    def test_missing_security_headers(self):
        findings = MissingSecurityHeadersAnalyzer().analyze(_make_ctx(body=b'{"ok":true}'))
        assert len(findings) == 1
        assert findings[0].category == Category.SECURITY

    def test_cors_wildcard_with_creds_critical(self):
        ctx = _make_ctx(headers={
            "access-control-allow-origin": "*",
            "access-control-allow-credentials": "true",
        }, body=b"ok")
        findings = CORSMisconfigAnalyzer().analyze(ctx)
        assert len(findings) == 1
        assert findings[0].severity == Severity.CRITICAL

    def test_jwt_alg_none_is_critical(self):
        import base64

        header = base64.urlsafe_b64encode(json.dumps({"alg": "none"}).encode()).rstrip(b"=").decode()
        payload = base64.urlsafe_b64encode(json.dumps({"sub": "u1", "exp": int(time.time()) + 600}).encode()).rstrip(b"=").decode()
        token = f"{header}.{payload}.sig"
        ctx = _make_ctx(body=json.dumps({"access_token": token}).encode())
        findings = JWTDecodeAnalyzer().analyze(ctx)
        assert any(f.severity == Severity.CRITICAL for f in findings)


class TestV2Performance:
    def test_large_uncompressed(self):
        ctx = _make_ctx(headers={"content-type": "application/json"}, body=b"x" * 20_000)
        findings = CompressionAnalyzer().analyze(ctx)
        assert len(findings) == 1
        assert "not compressed" in findings[0].title.lower()

    def test_pagination_detected(self):
        body = json.dumps({"data": [{"id": 1}], "page": 2, "total_pages": 10, "total": 95}).encode()
        findings = PaginationDetectionAnalyzer().analyze(_make_ctx(body=body))
        assert len(findings) == 1


class TestV2Consistency:
    def test_redirect_without_location(self):
        findings = RedirectLocationAnalyzer().analyze(_make_ctx(status=302, body=b""))
        assert len(findings) == 1

    def test_schema_drift_from_second_list_item(self):
        stored = {"[]": "dict", "[].id": "int"}
        body = json.dumps([{"id": 1}, {"id": 2, "email": "x@example.com"}]).encode()
        findings = SchemaDriftAnalyzer().analyze(_make_ctx(body=body, stored_schema=stored))
        assert len(findings) == 1
        assert "[].email" in findings[0].details["added"]


class TestV2ServerAndHints:
    def test_response_time_anomaly_warning(self):
        values = [100, 105, 98, 110, 102, 99, 103, 107, 101, 104]
        stats = {"elapsed_values": json.dumps(values), "call_count": 10}
        findings = ResponseTimeAnomalyAnalyzer().analyze(_make_ctx(endpoint_stats=stats, elapsed=0.5, body=b"{}"))
        assert len(findings) == 1
        assert findings[0].severity == Severity.WARNING

    def test_n_plus_one_detection(self):
        history = [{"method": "GET", "url": f"https://api.com/users/{i}"} for i in range(10)]
        findings = NPlusOneDetectionAnalyzer().analyze(_make_ctx(history_rows=history, body=b"{}"))
        assert len(findings) == 1
        assert "N+1" in findings[0].title

