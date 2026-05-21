"""Tests for response_intelligence/shared/http.py and shared/stats.py,
and response_intelligence analyzers: sensitive_data.py.
"""

from __future__ import annotations

import pytest

from equinox.core.response_intelligence.shared.http import (
    first_present_header,
    parse_cache_control,
    summarize_cache_control,
)
from equinox.core.response_intelligence.shared.stats import (
    coerce_numeric_samples,
    percentile,
)

# ── shared/http.py ────────────────────────────────────────────────────────────


class TestFirstPresentHeader:
    def test_returns_first_matching(self) -> None:
        headers = {"x-foo": "bar", "content-type": "json"}
        assert first_present_header(headers, ["x-foo", "content-type"]) == "bar"

    def test_returns_none_when_none_match(self) -> None:
        assert first_present_header({}, ["missing"]) is None

    def test_skips_missing_keys(self) -> None:
        headers = {"b": "2"}
        assert first_present_header(headers, ["a", "b"]) == "2"


class TestParseCacheControl:
    def test_parses_directives(self) -> None:
        result = parse_cache_control("no-cache, max-age=3600")
        assert "no-cache" in result
        assert "max-age=3600" in result

    def test_empty_string_returns_empty_list(self) -> None:
        assert parse_cache_control("") == []

    def test_none_returns_empty_list(self) -> None:
        assert parse_cache_control(None) == []  # type: ignore[arg-type]


class TestSummarizeCacheControl:
    def test_no_store(self) -> None:
        assert summarize_cache_control("no-store, no-cache") == "no-store"

    def test_no_cache(self) -> None:
        assert summarize_cache_control("no-cache") == "revalidate"

    def test_max_age_days(self) -> None:
        result = summarize_cache_control("max-age=172800")  # 2 days
        assert "d" in result

    def test_max_age_hours(self) -> None:
        result = summarize_cache_control("max-age=7200")  # 2 hours
        assert "h" in result

    def test_max_age_seconds(self) -> None:
        result = summarize_cache_control("max-age=120")
        assert "120s" in result

    def test_empty_returns_present(self) -> None:
        assert summarize_cache_control("") == "present"

    def test_none_returns_present(self) -> None:
        assert summarize_cache_control(None) == "present"  # type: ignore[arg-type]


# ── shared/stats.py ───────────────────────────────────────────────────────────


class TestCoerceNumericSamples:
    def test_empty_list(self) -> None:
        assert coerce_numeric_samples([]) == []

    def test_non_list_returns_empty(self) -> None:
        assert coerce_numeric_samples("not a list") == []  # type: ignore[arg-type]
        assert coerce_numeric_samples(None) == []  # type: ignore[arg-type]

    def test_filters_non_numeric(self) -> None:
        result = coerce_numeric_samples([1, "bad", 2.5, None])
        assert result == [1.0, 2.5]

    def test_truncates_to_max_samples(self) -> None:
        values = list(range(600))
        result = coerce_numeric_samples(values, max_samples=500)
        assert len(result) == 500

    def test_keeps_tail_when_truncated(self) -> None:
        values = list(range(600))
        result = coerce_numeric_samples(values, max_samples=500)
        assert result[-1] == 599.0

    def test_filters_nan_and_inf(self) -> None:
        import math

        result = coerce_numeric_samples([1.0, float("nan"), float("inf"), 2.0])
        assert all(math.isfinite(v) for v in result)
        assert len(result) == 2


class TestPercentile:
    def test_empty_returns_zero(self) -> None:
        assert percentile([], 50) == 0.0

    def test_single_element(self) -> None:
        assert percentile([5.0], 50) == 5.0

    def test_p50_of_sorted(self) -> None:
        data = [1.0, 2.0, 3.0, 4.0, 5.0]
        result = percentile(data, 50)
        assert result == pytest.approx(3.0)

    def test_p100_returns_max(self) -> None:
        data = [1.0, 2.0, 3.0]
        assert percentile(data, 100) == 3.0

    def test_p0_returns_min(self) -> None:
        data = [1.0, 2.0, 3.0]
        assert percentile(data, 0) == 1.0

    def test_interpolation(self) -> None:
        data = [0.0, 10.0]
        result = percentile(data, 50)
        assert 0.0 < result < 10.0


# ── response_intelligence/analyzers/sensitive_data.py ────────────────────────


class TestSensitiveDataAnalyzer:
    def _make_ctx(self, body: str, resp_headers: dict | None = None) -> object:
        from unittest.mock import MagicMock

        from equinox.core.request.response import Response
        from equinox.core.response_intelligence.analyzers.sensitive_data import AnalysisContext

        response = Response(
            status_code=200,
            reason="OK",
            headers=resp_headers or {"Content-Type": "application/json"},
            body=body.encode("utf-8"),
            elapsed=0.1,
            request=MagicMock(),
        )
        return AnalysisContext(request=MagicMock(), response=response)

    def test_detects_email_in_response_body(self) -> None:
        from equinox.core.response_intelligence.analyzers.sensitive_data import (
            SensitiveDataCachingAnalyzer,
        )

        ctx = self._make_ctx('{"email": "user@example.com", "name": "Alice"}')
        analyzer = SensitiveDataCachingAnalyzer()
        hints = analyzer.analyze(ctx)
        # Should find at least one PII hint
        assert isinstance(hints, list)

    def test_no_hints_for_clean_response(self) -> None:
        from equinox.core.response_intelligence.analyzers.sensitive_data import (
            SensitiveDataCachingAnalyzer,
        )

        ctx = self._make_ctx('{"status": "ok", "count": 42}')
        analyzer = SensitiveDataCachingAnalyzer()
        hints = analyzer.analyze(ctx)
        assert isinstance(hints, list)
