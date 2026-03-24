"""Simple heuristic recommender for API requests based on local history.

This is a minimal, explainable implementation intended as an MVP. It
queries the `history_index` table (created by migrations) and computes
similarity scores to produce header/query/body suggestions.
"""

import json
import logging
from typing import Dict, Any, List, Tuple

from equinox.storage.database import Database
from equinox.core import urls

logger = logging.getLogger(__name__)

IGNORED_HEADERS = {"authorization", "cookie", "user-agent", "date", "content-length"}


def _path_similarity(a: List[str], b: List[str]) -> float:
    if not a and not b:
        return 1.0
    min_len = min(len(a), len(b))
    max_len = max(len(a), len(b))
    if max_len == 0:
        return 1.0
    score = 0.0
    for i in range(min_len):
        A, B = a[i], b[i]
        if A == B:
            score += 1.0
        elif A.startswith("{") and B.startswith("{"):
            score += 0.8
    # penalize length difference
    return (score / max_len) * (min_len / max_len)


def _query_similarity(a: Dict[str, str], b: Dict[str, str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    shared = set(a.keys()) & set(b.keys())
    if not shared:
        return 0.0
    score = 0.0
    for k in shared:
        score += 1.0 if a.get(k) == b.get(k) else 0.5
    denom = max(len(a), len(b))
    return score / denom


def _header_similarity(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    if not a and not b:
        return 1.0
    a_keys = {k.lower() for k in a.keys() if k.lower() not in IGNORED_HEADERS}
    b_keys = {k.lower() for k in b.keys() if k.lower() not in IGNORED_HEADERS}
    if not a_keys and not b_keys:
        return 1.0
    if not a_keys or not b_keys:
        return 0.0
    inter = a_keys & b_keys
    return len(inter) / max(len(a_keys), len(b_keys))


def _compute_similarity(new: Dict[str, Any], cand: Dict[str, Any]) -> Dict[str, float]:
    method_score = 1.0 if new.get("method", "").upper() == cand.get("method", "").upper() else 0.0
    path_score = _path_similarity(new.get("path_segments", []), cand.get("path_segments", []))
    query_score = _query_similarity(new.get("query_params", {}), cand.get("query_params", {}))
    header_score = _header_similarity(new.get("headers", {}), cand.get("request_headers", {}))
    body_score = 1.0 if new.get("body_hash") and cand.get("body_hash") and new.get("body_hash") == cand.get("body_hash") else 0.0

    total = (
        method_score * 0.3
        + path_score * 0.4
        + query_score * 0.1
        + header_score * 0.1
        + body_score * 0.1
    )

    return {
        "total": total,
        "breakdown": {
            "method": method_score,
            "path": path_score,
            "query": query_score,
            "headers": header_score,
            "body": body_score,
        },
    }


class Recommender:
    def __init__(self, db: Database, max_candidates: int = 500):
        self.db = db
        self.max_candidates = max_candidates

    def _get_candidates(self, new_request: Dict[str, Any]) -> List[Dict[str, Any]]:
        # Use normalized_url prefix to narrow search
        norm = urls.normalized_parts(new_request.get("url", ""))
        prefix = urls.base_path(norm.get("normalized_url", ""))

        rows = self.db.fetchall(
            "SELECT hi.*, h.request_headers, h.request_body FROM history_index hi LEFT JOIN history h ON h.id = hi.history_id WHERE hi.method = ? AND hi.normalized_url LIKE ? ORDER BY hi.executed_at DESC LIMIT ?",
            (new_request.get("method", "").upper(), prefix + "%", self.max_candidates),
        )

        candidates = []
        for r in rows:
            try:
                r = dict(r)
                r["path_segments"] = json.loads(r.get("path_segments") or "[]")
                r["query_params"] = json.loads(r.get("query_params") or "{}")
                # request_headers is stored as JSON in history.request_headers
                try:
                    r["request_headers"] = json.loads(r.get("request_headers") or "{}")
                except Exception:
                    r["request_headers"] = {}
                candidates.append(r)
            except Exception:
                continue
        return candidates

    def find_best_matches(self, new_request: Dict[str, Any], min_score: float = 0.5, limit: int = 10) -> List[Tuple[Dict[str, Any], Dict[str, float]]]:
        # Enrich new_request with normalized parts
        norm = urls.normalized_parts(new_request.get("url", ""))
        new_request["normalized_url"] = norm.get("normalized_url")
        new_request["path_segments"] = norm.get("path_segments")
        new_request["query_params"] = norm.get("query_params")

        candidates = self._get_candidates(new_request)
        scored = []
        for c in candidates:
            score = _compute_similarity(new_request, c)
            if score["total"] >= min_score:
                scored.append((c, score))

        scored.sort(key=lambda cs: cs[1]["total"], reverse=True)
        return scored[:limit]

    def generate_suggestions(self, new_request: Dict[str, Any], top_n: int = 5) -> List[Dict[str, Any]]:
        matches = self.find_best_matches(new_request, min_score=0.4, limit=50)
        if not matches:
            return []

        # Only consider successful matches
        successful = [m for m in matches if int(m[0].get("response_success", 0)) == 1]
        if not successful:
            return []

        suggestions = []
        total = len(successful)

        # Header suggestions
        header_freq: Dict[str, Dict[str, int]] = {}
        for cand, score in successful:
            for k, v in (cand.get("request_headers") or {}).items():
                kl = k.lower()
                if kl in IGNORED_HEADERS:
                    continue
                hdr = header_freq.setdefault(kl, {})
                hdr[ str(v) ] = hdr.get(str(v), 0) + 1

        for key, vals in header_freq.items():
            freq = sum(vals.values())
            most_val = max(vals.items(), key=lambda kv: kv[1])[0]
            confidence = (freq / total) * 1.0
            suggestions.append({
                "type": "header",
                "key": key,
                "suggested_value": most_val,
                "confidence": confidence,
                "based_on": total,
            })

        # Query param suggestions
        param_freq: Dict[str, int] = {}
        for cand, score in successful:
            for k, v in (cand.get("query_params") or {}).items():
                param_freq[k] = param_freq.get(k, 0) + 1

        for key, freq in param_freq.items():
            suggestions.append({
                "type": "query",
                "key": key,
                "suggested_value": None,
                "confidence": (freq / total) * 0.9,
                "based_on": total,
            })

        # Rank and return top_n
        suggestions.sort(key=lambda s: s["confidence"], reverse=True)
        return suggestions[:top_n]

