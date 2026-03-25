import json

import pytest

from equinox.intelligence import recommender


def test_path_similarity_exact_and_placeholder():
    a = ["users", "{id}", "posts"]
    b = ["users", "{id}", "posts"]
    assert recommender._path_similarity(a, b) == pytest.approx(1.0)


def test_query_similarity_shared_keys():
    a = {"q": "1", "p": "x"}
    b = {"q": "1", "r": "y"}
    # shared key q matches exactly -> score 1 / denom(2) = 0.5
    assert recommender._query_similarity(a, b) == pytest.approx(0.5)


def test_header_similarity_ignores_common_headers():
    a = {"Authorization": "secret", "X-Custom": "a"}
    b = {"authorization": "other", "X-Custom": "a"}
    # Authorization should be ignored; only X-Custom remains and matches
    assert recommender._header_similarity(a, b) == pytest.approx(1.0)


class FakeDB:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self, query, params=()):
        # ignore query/params in this fake — return prepared rows
        return [dict(r) for r in self._rows]


def make_candidate(path_segments=None, query_params=None, headers=None, success=1):
    return {
        "method": "GET",
        "normalized_url": "https://api.example.com/users/{id}",
        "path_segments": json.dumps(path_segments or ["users", "{id}"]),
        "query_params": json.dumps(query_params or {}),
        "request_headers": json.dumps(headers or {"Accept": "application/json"}),
        "request_body": "{}",
        "response_success": success,
        "history_id": 1,
        "executed_at": "2026-01-01T00:00:00",
    }


def test_recommender_find_and_suggest_headers():
    cand = make_candidate()
    db = FakeDB([cand])
    rec = recommender.Recommender(db)

    new_request = {"method": "GET", "url": "https://api.example.com/users/42"}
    matches = rec.find_best_matches(new_request, min_score=0.0, limit=10)
    assert matches, "Expected at least one match"

    suggestions = rec.generate_suggestions(new_request, top_n=5)
    # Should suggest at least the Accept header
    assert any(s.get("type") == "header" for s in suggestions)
    keys = {s.get("key") for s in suggestions if s.get("type") == "header"}
    assert "accept" in keys

