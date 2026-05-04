"""Request recommender — suggests headers and query params based on history.

The Recommender queries the history database for structurally similar past
requests and analyses their successful responses to generate confidence-ranked
suggestions for headers and query parameters.

Similarity is computed across five dimensions (method, path, query, headers,
body) weighted to produce a single score in [0, 1].
"""

import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple, cast

from equinox.storage.database import Database
from equinox.storage.utils import safe_json_loads
from equinox.core import urls

logger = logging.getLogger(__name__)

# Conservative token pattern for HTTP header names.
_HEADER_NAME_RE = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")

# Conservative key pattern for query parameters.
_QUERY_KEY_RE = re.compile(r"^[A-Za-z0-9_.~-]+$")

# ──────────────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────────────

# Headers excluded from similarity scoring and suggestions (security-sensitive
# or request-specific headers that are not meaningful for recommendations).
IGNORED_HEADERS: frozenset = frozenset({
    "authorization", "cookie", "user-agent", "date", "content-length",
})

# Similarity dimension weights — must sum to 1.0.
_WEIGHT_METHOD:  float = 0.3
_WEIGHT_PATH:    float = 0.4
_WEIGHT_QUERY:   float = 0.1
_WEIGHT_HEADERS: float = 0.1
_WEIGHT_BODY:    float = 0.1

# Confidence multipliers applied to suggestion types.
_HEADER_CONFIDENCE_SCALE: float = 1.0
_QUERY_CONFIDENCE_SCALE:  float = 0.9

# Recommender defaults.
_DEFAULT_MAX_CANDIDATES:  int   = 500
_DEFAULT_MIN_SCORE:       float = 0.5
_DEFAULT_RESULT_LIMIT:    int   = 10
_DEFAULT_TOP_N:           int   = 5
_MAX_RESULT_LIMIT:        int   = 100
_MAX_TOP_N:               int   = 20
_MIN_SUGGESTION_CONFIDENCE: float = 0.2
_MAX_SUGGESTION_VALUE_LEN: int = 512

# Internal thresholds used by generate_suggestions when it calls find_best_matches.
_SUGGESTION_MIN_SCORE: float = 0.4
_SUGGESTION_FETCH_LIMIT: int  = 50
_SUCCESS_WEIGHT_FLOOR: float = 0.01

# SQL used by _get_candidates — separated for readability and easy tuning.
_CANDIDATES_SQL = """
    SELECT hi.*, h.request_headers, h.request_body
      FROM history_index hi
      LEFT JOIN history h ON h.id = hi.history_id
     WHERE hi.method = ?
       AND hi.normalized_url LIKE ?
     ORDER BY hi.executed_at DESC
     LIMIT ?
"""


# ──────────────────────────────────────────────────────────────────────────────
# Private helpers
# ──────────────────────────────────────────────────────────────────────────────

def _path_similarity(a: List[str], b: List[str]) -> float:
    """Score structural similarity of two URL path segment lists in [0, 1].

    Exact segment matches score 1.0; two placeholder segments (``{…}``) score
    0.8 (same structural role, may differ in name).  Missing segments (length
    difference) are penalised via a length-ratio multiplier.

    Args:
        a: Normalised path segments for the first URL.
        b: Normalised path segments for the second URL.
    """
    if not a and not b:
        return 1.0
    max_len = max(len(a), len(b))
    if max_len == 0:
        return 1.0
    min_len = min(len(a), len(b))
    score = 0.0
    for i in range(min_len):
        if a[i] == b[i]:
            score += 1.0
        elif a[i].startswith("{") and b[i].startswith("{"):
            score += 0.8
    # Penalise length difference with a quadratic factor.
    return (score / max_len) * (min_len / max_len)


def _query_similarity(a: Dict[str, str], b: Dict[str, str]) -> float:
    """Score similarity of two query-parameter dicts in [0, 1].

    Shared keys with identical values score 1.0; shared keys with different
    values score 0.5; unshared keys contribute 0.

    Args:
        a: Query parameters for the first URL.
        b: Query parameters for the second URL.
    """
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    shared = set(a.keys()) & set(b.keys())
    if not shared:
        return 0.0
    score = sum(1.0 if a[k] == b[k] else 0.5 for k in shared)
    return score / max(len(a), len(b))


def _header_similarity(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    """Score Jaccard similarity of non-ignored header key sets in [0, 1].

    Header names are compared case-insensitively. Headers listed in
    IGNORED_HEADERS are excluded before comparison.

    Args:
        a: Headers dict for the first request.
        b: Headers dict for the second request.
    """
    if not a and not b:
        return 1.0
    # Lower-case all keys then exclude ignored ones with a set difference.
    a_keys = {k.lower() for k in a} - IGNORED_HEADERS
    b_keys = {k.lower() for k in b} - IGNORED_HEADERS
    if not a_keys and not b_keys:
        return 1.0
    if not a_keys or not b_keys:
        return 0.0
    return len(a_keys & b_keys) / max(len(a_keys), len(b_keys))


def _body_similarity(new: Dict[str, Any], cand: Dict[str, Any]) -> float:
    """Return 1.0 when both requests share a non-null body hash, else 0.0.

    Args:
        new:  Enriched new-request dict (may contain ``body_hash``).
        cand: History candidate dict (may contain ``body_hash``).
    """
    new_hash = new.get("body_hash")
    cand_hash = cand.get("body_hash")
    return 1.0 if (new_hash and cand_hash and new_hash == cand_hash) else 0.0


def _compute_similarity(new: Dict[str, Any], cand: Dict[str, Any]) -> Dict[str, Any]:
    """Compute a weighted similarity score between *new* and a history *cand*.

    Returns a dict with keys ``"total"`` (float in [0, 1]) and ``"breakdown"``
    (per-dimension scores for diagnostics).

    Args:
        new:  Enriched new-request dict.
        cand: History candidate row dict.
    """
    method  = 1.0 if new.get("method", "").upper() == cand.get("method", "").upper() else 0.0
    path    = _path_similarity(new.get("path_segments", []), cand.get("path_segments", []))
    query   = _query_similarity(new.get("query_params", {}), cand.get("query_params", {}))
    headers = _header_similarity(new.get("headers", {}), cand.get("request_headers", {}))
    body    = _body_similarity(new, cand)

    total = (
        method  * _WEIGHT_METHOD
        + path    * _WEIGHT_PATH
        + query   * _WEIGHT_QUERY
        + headers * _WEIGHT_HEADERS
        + body    * _WEIGHT_BODY
    )

    return {
        "total": total,
        "breakdown": {
            "method":  method,
            "path":    path,
            "query":   query,
            "headers": headers,
            "body":    body,
        },
    }


def _parse_candidate_row(row: Any) -> Optional[Dict[str, Any]]:
    """Parse JSON fields of a raw history row into a usable candidate dict.

    Returns ``None`` if parsing fails so callers can skip the row silently.

    Args:
        row: A row returned by ``Database.fetchall`` (dict-like).
    """
    try:
        r = dict(row)

        segments = safe_json_loads(r.get("path_segments") or "[]")
        r["path_segments"] = segments if isinstance(segments, list) else []

        qparams = safe_json_loads(r.get("query_params") or "{}")
        r["query_params"] = qparams if isinstance(qparams, dict) else {}

        req_headers = safe_json_loads(r.get("request_headers") or "{}")
        r["request_headers"] = req_headers if isinstance(req_headers, dict) else {}

        return cast(Dict[str, Any], r)
    except Exception:
        logger.debug("recommender_parse_candidate_failed", exc_info=True)
        return None


def _is_successful(candidate: Dict[str, Any]) -> bool:
    """Return True when the candidate history entry has a successful response.

    Args:
        candidate: Parsed history candidate dict.
    """
    return int(candidate.get("response_success", 0)) == 1


def _most_frequent_value(freq_map: Dict[str, float]) -> str:
    """Return the key with the highest count in *freq_map*.

    Args:
        freq_map: Mapping of value → occurrence count.
    """
    return max(freq_map.items(), key=lambda kv: kv[1])[0]


def _normalize_header_key(key: Any) -> Optional[str]:
    """Return a canonical lower-case header name, or ``None`` if invalid."""
    key_str = str(key).strip().lower()
    if not key_str or key_str in IGNORED_HEADERS:
        return None
    if not _HEADER_NAME_RE.match(key_str):
        return None
    return key_str


def _normalize_query_key(key: Any) -> Optional[str]:
    """Return a normalized query key, or ``None`` when invalid/unsafe."""
    key_str = str(key).strip()
    if not key_str:
        return None
    if not _QUERY_KEY_RE.match(key_str):
        return None
    return key_str


def _stringify_value(value: Any) -> str:
    """Return a bounded string representation safe for UI suggestion rendering."""
    text = str(value).strip()
    return text[:_MAX_SUGGESTION_VALUE_LEN]


def _request_header_set(new_request: Dict[str, Any]) -> Set[str]:
    """Extract normalized header keys present on the new request."""
    headers = new_request.get("headers") or {}
    if not isinstance(headers, dict):
        return set()
    result: Set[str] = set()
    for key in headers:
        norm = _normalize_header_key(key)
        if norm is not None:
            result.add(norm)
    return result


def _request_query_set(new_request: Dict[str, Any]) -> Set[str]:
    """Extract normalized query keys present on the new request."""
    result: Set[str] = set()

    parsed = urls.normalized_parts(str(new_request.get("url") or ""))
    query = parsed.get("query_params") or {}
    if isinstance(query, dict):
        for key in query:
            norm = _normalize_query_key(key)
            if norm is not None:
                result.add(norm)

    params = new_request.get("params") or {}
    if isinstance(params, dict):
        for key in params:
            norm = _normalize_query_key(key)
            if norm is not None:
                result.add(norm)

    return result


def suggestions_to_findings(
    suggestions: List[Dict[str, Any]],
    high_confidence: float = 0.75,
) -> List[Any]:
    """Convert recommender suggestions into Response Intelligence findings."""
    from equinox.core.response_intelligence.models import Category, Finding, Severity

    findings: List[Finding] = []
    for suggestion in suggestions:
        confidence = float(suggestion.get("confidence") or 0.0)
        key = str(suggestion.get("key") or "")
        stype = suggestion.get("type")

        if stype == "header":
            title = f"Suggested header: {key}"
            desc = (
                f"Set header {key} = {suggestion.get('suggested_value')} "
                f"(confidence {confidence:.2f})"
            )
        elif stype == "query":
            title = f"Suggested query parameter: {key}"
            desc = (
                f"Add query param {key} "
                f"(seen in {suggestion.get('based_on')} requests, "
                f"confidence {confidence:.2f})"
            )
        else:
            title = "Suggested change"
            desc = str(suggestion)

        severity = Severity.WARNING if confidence >= high_confidence else Severity.INFO
        findings.append(
            Finding(
                Category.HINTS,
                severity,
                title,
                desc,
                analyzer_id="recommender",
                details=dict(suggestion),
            )
        )

    return findings


# ──────────────────────────────────────────────────────────────────────────────
# Recommender
# ──────────────────────────────────────────────────────────────────────────────

class Recommender:
    """Generates header and query-parameter suggestions from request history.

    Suggestions are derived by:
    1. Fetching structurally similar past requests from the history DB.
    2. Filtering to those with successful responses.
    3. Ranking headers and query params by how often they appear in those
       responses, weighted by a per-type confidence scale.

    Args:
        db:             Open database instance.
        max_candidates: Maximum history rows to evaluate per call.
    """

    def __init__(self, db: Database, max_candidates: int = _DEFAULT_MAX_CANDIDATES) -> None:
        self.db = db
        self.max_candidates = max_candidates

    # ── Private helpers ───────────────────────────────────────────────────────

    def _get_candidates(self, enriched_request: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Fetch and parse history rows that share the method and URL prefix.

        Args:
            enriched_request: New-request dict already populated with
                              ``normalized_url`` by :meth:`find_best_matches`.
        """
        norm_url = enriched_request.get("normalized_url", "")
        prefix = urls.base_path(norm_url)

        try:
            rows = self.db.fetchall(
                _CANDIDATES_SQL,
                (
                    enriched_request.get("method", "").upper(),
                    prefix + "%",
                    self.max_candidates,
                ),
            )
        except Exception:
            logger.warning(
                "recommender_candidate_query_failed",
                extra={
                    "operation": "recommender_candidates",
                    "method": enriched_request.get("method", ""),
                    "prefix": prefix,
                    "limit": self.max_candidates,
                },
                exc_info=True,
            )
            return []

        candidates = []
        for row in rows:
            parsed = _parse_candidate_row(row)
            if parsed is not None:
                candidates.append(parsed)
        return candidates

    # ── Public API ────────────────────────────────────────────────────────────

    def find_best_matches(
        self,
        new_request: Dict[str, Any],
        min_score: float = _DEFAULT_MIN_SCORE,
        limit: int = _DEFAULT_RESULT_LIMIT,
    ) -> List[Tuple[Dict[str, Any], Dict[str, Any]]]:
        """Return the *limit* best history matches for *new_request*.

        Builds a local enriched copy of *new_request* (does **not** mutate the
        caller's dict), fetches candidates, scores them, and returns those
        meeting *min_score* sorted by descending total score.

        Args:
            new_request: Dict with at least ``"method"`` and ``"url"`` keys.
            min_score:   Minimum total similarity to include in results.
            limit:       Maximum number of results to return.

        Returns:
            List of ``(candidate_dict, score_dict)`` tuples, best first.
        """
        method = str(new_request.get("method") or "").upper().strip()
        raw_url = str(new_request.get("url") or "").strip()
        if not method or not raw_url:
            return []

        min_score = max(0.0, min(1.0, float(min_score)))
        limit = max(1, min(int(limit), _MAX_RESULT_LIMIT))

        norm = urls.normalized_parts(raw_url)
        # Build an enriched local copy — never mutate the caller's dict.
        enriched: Dict[str, Any] = {
            **new_request,
            "method": method,
            "normalized_url": norm.get("normalized_url"),
            "path_segments":  norm.get("path_segments"),
            "query_params":   norm.get("query_params"),
        }

        scored: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
        for cand in self._get_candidates(enriched):
            score = _compute_similarity(enriched, cand)
            if score["total"] >= min_score:
                scored.append((cand, score))
        scored.sort(key=lambda cs: cs[1]["total"], reverse=True)
        return scored[:limit]

    def generate_suggestions(
        self,
        new_request: Dict[str, Any],
        top_n: int = _DEFAULT_TOP_N,
    ) -> List[Dict[str, Any]]:
        """Generate ranked header and query-parameter suggestions.

        Analyses successful history matches to propose headers and query params
        ranked by how consistently they appear, scaled by a per-type weight.

        Args:
            new_request: Dict with at least ``"method"`` and ``"url"`` keys.
            top_n:       Maximum number of suggestions to return.

        Returns:
            List of suggestion dicts ordered by descending confidence, each
            containing ``"type"``, ``"key"``, ``"suggested_value"``,
            ``"confidence"``, and ``"based_on"`` fields.
        """
        top_n = max(1, min(int(top_n), _MAX_TOP_N))

        existing_header_keys = _request_header_set(new_request)
        existing_query_keys = _request_query_set(new_request)

        matches = self.find_best_matches(
            new_request,
            min_score=_SUGGESTION_MIN_SCORE,
            limit=_SUGGESTION_FETCH_LIMIT,
        )
        if not matches:
            return []

        successful = [(cand, score) for cand, score in matches if _is_successful(cand)]
        if not successful:
            return []

        total = len(successful)
        total_weight = sum(
            max(float(score.get("total") or 0.0), _SUCCESS_WEIGHT_FLOOR)
            for _, score in successful
        )
        if total_weight <= 0:
            return []

        suggestions: List[Dict[str, Any]] = []

        # ── Header suggestions ────────────────────────────────────────────────
        # Build a frequency map: header_name → {value → count}
        header_freq: Dict[str, Dict[str, float]] = {}
        for cand, score in successful:
            weight = max(float(score.get("total") or 0.0), _SUCCESS_WEIGHT_FLOOR)
            for k, v in (cand.get("request_headers") or {}).items():
                norm_key = _normalize_header_key(k)
                if norm_key is None or norm_key in existing_header_keys:
                    continue
                value_counts = header_freq.setdefault(norm_key, {})
                str_v = _stringify_value(v)
                value_counts[str_v] = value_counts.get(str_v, 0.0) + weight

        for key, value_counts in header_freq.items():
            freq = float(sum(value_counts.values()))
            confidence = (freq / total_weight) * _HEADER_CONFIDENCE_SCALE
            if confidence < _MIN_SUGGESTION_CONFIDENCE:
                continue
            suggestions.append({
                "type":            "header",
                "key":             key,
                "suggested_value": _most_frequent_value(value_counts),
                "confidence":      confidence,
                "based_on":        total,
            })

        # ── Query parameter suggestions ───────────────────────────────────────
        param_freq: Dict[str, float] = {}
        for cand, score in successful:
            weight = max(float(score.get("total") or 0.0), _SUCCESS_WEIGHT_FLOOR)
            for k in (cand.get("query_params") or {}):
                norm_key = _normalize_query_key(k)
                if norm_key is None or norm_key in existing_query_keys:
                    continue
                param_freq[norm_key] = param_freq.get(norm_key, 0.0) + weight

        for key, freq in param_freq.items():
            confidence = (float(freq) / total_weight) * _QUERY_CONFIDENCE_SCALE
            if confidence < _MIN_SUGGESTION_CONFIDENCE:
                continue
            suggestions.append({
                "type":            "query",
                "key":             key,
                "suggested_value": None,
                "confidence":      confidence,
                "based_on":        total,
            })

        suggestions.sort(
            key=lambda s: (s["confidence"], s["type"], s["key"]),
            reverse=True,
        )

        logger.debug(
            "recommender_generated_suggestions",
            extra={
                "operation": "recommender_suggestions",
                "method": str(new_request.get("method") or "").upper(),
                "suggestion_count": len(suggestions),
                "top_n": top_n,
            },
        )

        return suggestions[:top_n]

