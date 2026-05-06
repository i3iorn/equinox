"""Tests for engine-level JSON gating in response intelligence analyzers."""

from typing import List

from equinox.core.request import Request, Response
from equinox.core.response_intelligence.base import Analyzer
from equinox.core.response_intelligence.engine import AnalysisEngine
from equinox.core.response_intelligence.models import AnalysisContext, Category, Finding, Severity


class _RequiresJsonAnalyzer(Analyzer):
    analyzer_id = "test.requires_json"
    category = Category.CONSISTENCY
    display_name = "Requires JSON"
    requires_valid_json_body = True

    def __init__(self) -> None:
        self.calls = 0

    def analyze(self, ctx: AnalysisContext) -> List[Finding]:
        self.calls += 1
        return []


class _AlwaysAnalyzer(Analyzer):
    analyzer_id = "test.always"
    category = Category.HINTS
    display_name = "Always Runs"

    def __init__(self) -> None:
        self.calls = 0

    def analyze(self, ctx: AnalysisContext) -> List[Finding]:
        self.calls += 1
        return [
            Finding(
                category=self.category,
                severity=Severity.INFO,
                title="ran",
                description="always runs",
                analyzer_id=self.analyzer_id,
            )
        ]


def _make_ctx(body: bytes) -> AnalysisContext:
    request = Request(method="GET", url="https://api.example.com/items", headers={})
    response = Response(
        status_code=200,
        reason="OK",
        headers={"content-type": "application/json"},
        body=body,
        elapsed=0.1,
        request=request,
    )
    return AnalysisContext(request=request, response=response)


def test_engine_skips_json_required_analyzers_for_invalid_json(monkeypatch):
    ctx = _make_ctx(b"{invalid json")
    engine = AnalysisEngine()

    requires_json = _RequiresJsonAnalyzer()
    always = _AlwaysAnalyzer()
    engine._analyzers = [requires_json, always]

    original_json = ctx.response.json
    json_call_count = {"count": 0}

    def _counted_json():
        json_call_count["count"] += 1
        return original_json()

    monkeypatch.setattr(ctx.response, "json", _counted_json)

    findings = engine.analyze(ctx)

    assert json_call_count["count"] == 1
    assert requires_json.calls == 0
    assert always.calls == 1
    assert len(findings) == 1


def test_engine_runs_json_required_analyzers_for_valid_json(monkeypatch):
    ctx = _make_ctx(b'{"ok": true}')
    engine = AnalysisEngine()

    requires_json = _RequiresJsonAnalyzer()
    always = _AlwaysAnalyzer()
    engine._analyzers = [requires_json, always]

    original_json = ctx.response.json
    json_call_count = {"count": 0}

    def _counted_json():
        json_call_count["count"] += 1
        return original_json()

    monkeypatch.setattr(ctx.response, "json", _counted_json)

    findings = engine.analyze(ctx)

    assert json_call_count["count"] == 1
    assert requires_json.calls == 1
    assert always.calls == 1
    assert len(findings) == 1

