"""Response data capture — extract values from a response into session variables.

Capture rules are stored as plain dicts on each ``Request.captures`` list.
``CaptureEngine.apply_all()`` runs them after a response is received and
returns a list of ``CaptureResult`` objects.  Results are never raised as
exceptions; failures are recorded in-band.

Supported sources
-----------------
json    — dot-notation path with optional ``[n]`` array indices, e.g. ``user.id``,
          ``tokens[0].value``, ``data.items[2].name``
header  — case-insensitive header name lookup
regex   — ``re.search``; returns ``group(1)`` when a capture group is present,
          otherwise ``group(0)``
status  — the HTTP status code as a string
"""

from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class Capture:
    """A single capture rule."""

    variable: str   # destination variable name, e.g. "auth_token"
    source: str     # "json" | "header" | "regex" | "status"
    path: str = ""  # JSON dot-path, header name, or regex pattern
    default: str = ""  # value to use when extraction fails (empty = no fallback)


@dataclass
class CaptureResult:
    """Result of applying one :class:`Capture` rule."""

    variable: str
    value: str
    success: bool
    error: str = ""


class CaptureEngine:
    """Apply capture rules to an HTTP response."""

    # ── Public API ────────────────────────────────────────────────────

    @classmethod
    def apply_all(cls, captures: List[Capture], response: Any) -> List[CaptureResult]:
        """Apply every rule in *captures* to *response*.

        Never raises.  Each result records ``success=True/False`` and, on
        failure, the ``error`` message.  When a rule has a non-empty
        ``default`` and extraction fails, the default is used as the value
        and ``success`` is ``False``.

        Args:
            captures: Ordered list of :class:`Capture` rules.
            response: A :class:`~equinox.core.request.Response` instance.

        Returns:
            One :class:`CaptureResult` per rule, in the same order.
        """
        results: List[CaptureResult] = []
        for cap in captures:
            try:
                value = cls._extract(cap, response)
                results.append(CaptureResult(variable=cap.variable, value=value, success=True))
            except Exception as exc:
                err_msg = str(exc)
                logger.debug("Capture '%s' failed: %s", cap.variable, err_msg)
                results.append(CaptureResult(
                    variable=cap.variable,
                    value=cap.default,
                    success=False,
                    error=err_msg,
                ))
        return results

    # ── Extraction dispatch ───────────────────────────────────────────

    @classmethod
    def _extract(cls, cap: Capture, response: Any) -> str:
        if cap.source == "json":
            return cls._extract_json(cap.path, response.json())
        elif cap.source == "header":
            return cls._extract_header(cap.path, response)
        elif cap.source == "regex":
            return cls._extract_regex(cap.path, response)
        elif cap.source == "status":
            return str(response.status_code)
        else:
            raise ValueError(f"Unknown capture source: {cap.source!r}")

    # ── Source extractors ─────────────────────────────────────────────

    @staticmethod
    def _extract_json(path: str, data: Any) -> str:
        """Navigate *data* using a dot-notation path with optional ``[n]`` indices.

        Supported syntax examples:

        * ``"id"``                  → ``data["id"]``
        * ``"user.id"``             → ``data["user"]["id"]``
        * ``"tokens[0]"``           → ``data["tokens"][0]``
        * ``"data.items[2].name"``  → ``data["data"]["items"][2]["name"]``

        If *path* is empty, the entire *data* value is JSON-serialised and
        returned.

        Args:
            path: Dot-notation path string.
            data: Parsed JSON value (dict, list, or scalar).

        Returns:
            String representation of the extracted value.

        Raises:
            KeyError:    A dict key was not found.
            IndexError:  An array index was out of range.
            TypeError:   An intermediate value was not the expected type.
            ValueError:  A path segment could not be parsed.
        """
        if not path:
            return data if isinstance(data, str) else json.dumps(data)

        # Tokenise: split on ".", then handle optional "[n]" suffix per token.
        # Each token matches: word_chars optionally followed by [digits].
        _SEG = re.compile(r'^(\w+)(?:\[(\d+)\])?$')

        segments: List[tuple] = []
        for part in path.split("."):
            m = _SEG.match(part)
            if not m:
                raise ValueError(f"Invalid JSON path segment: {part!r}")
            key = m.group(1)
            idx: Optional[int] = int(m.group(2)) if m.group(2) is not None else None
            segments.append((key, idx))

        current = data
        for key, idx in segments:
            if not isinstance(current, dict):
                raise TypeError(
                    f"Expected a JSON object at key {key!r}, got {type(current).__name__}"
                )
            if key not in current:
                raise KeyError(f"Key {key!r} not found in JSON object")
            current = current[key]

            if idx is not None:
                if not isinstance(current, list):
                    raise TypeError(
                        f"Expected a JSON array for index [{idx}], got {type(current).__name__}"
                    )
                if idx >= len(current):
                    raise IndexError(
                        f"Index [{idx}] is out of range (array length {len(current)})"
                    )
                current = current[idx]

        return current if isinstance(current, str) else json.dumps(current)

    @staticmethod
    def _extract_header(name: str, response: Any) -> str:
        """Return the value of a response header (case-insensitive).

        ``Response.__post_init__`` already lowercases all header keys, so we
        just normalise the requested name to lower-case before lookup.

        Args:
            name:     Header name, e.g. ``"Content-Type"`` or ``"x-request-id"``.
            response: Response object with a ``headers`` dict.

        Returns:
            Header value string, or empty string if not found.
        """
        return response.headers.get(name.lower(), "")

    MAX_REGEX_PATTERN_LENGTH = 500
    _REGEX_TIMEOUT_SECONDS = 5.0

    @staticmethod
    def _extract_regex(pattern: str, response: Any) -> str:
        """Search *pattern* in the response body text.

        Returns ``group(1)`` if the pattern defines a capture group,
        otherwise ``group(0)`` (the full match).

        A background thread with a timeout guard protects against
        catastrophic backtracking (ReDoS).

        Args:
            pattern: Regular expression pattern string.
            response: Response object with a ``text`` property.

        Returns:
            Extracted string.

        Raises:
            ValueError: Pattern did not match, is too long, or timed out.
        """
        if len(pattern) > CaptureEngine.MAX_REGEX_PATTERN_LENGTH:
            raise ValueError(
                f"Regex pattern too long ({len(pattern)} chars, "
                f"max {CaptureEngine.MAX_REGEX_PATTERN_LENGTH})"
            )
        try:
            compiled = re.compile(pattern)
        except re.error as exc:
            raise ValueError(f"Invalid regex pattern: {exc}")

        # Limit search to first 1 MB of response text to bound CPU time
        text = response.text[:1_048_576] if len(response.text) > 1_048_576 else response.text

        # Run regex in a thread with a timeout to mitigate ReDoS
        result_container: List[Any] = [None]  # [match_or_None]
        error_container: List[Any] = [None]

        def _search() -> None:
            try:
                result_container[0] = compiled.search(text)
            except Exception as exc:
                error_container[0] = exc

        t = threading.Thread(target=_search, daemon=True)
        t.start()
        t.join(timeout=CaptureEngine._REGEX_TIMEOUT_SECONDS)

        if t.is_alive():
            raise ValueError(
                f"Regex pattern timed out after {CaptureEngine._REGEX_TIMEOUT_SECONDS}s "
                f"(possible catastrophic backtracking)"
            )
        if error_container[0] is not None:
            raise ValueError(f"Regex execution error: {error_container[0]}")

        m = result_container[0]
        if not m:
            raise ValueError(f"Pattern {pattern!r} did not match the response body")
        return m.group(1) if m.lastindex and m.lastindex >= 1 else m.group(0)

    # ── Serialisation helpers ─────────────────────────────────────────

    @classmethod
    def from_dict_list(cls, raw: List[Dict[str, Any]]) -> List[Capture]:
        """Convert a list of raw dicts (from DB JSON) to :class:`Capture` objects.

        Dicts with a missing or empty ``variable`` key are silently skipped.
        """
        captures: List[Capture] = []
        for d in raw:
            if not isinstance(d, dict):
                continue
            variable = d.get("variable", "")
            if not variable:
                continue
            captures.append(Capture(
                variable=variable,
                source=d.get("source", "json"),
                path=d.get("path", ""),
                default=d.get("default", ""),
            ))
        return captures

    @classmethod
    def to_dict_list(cls, captures: List[Capture]) -> List[Dict[str, Any]]:
        """Serialise :class:`Capture` objects to plain dicts for DB storage."""
        return [
            {
                "variable": c.variable,
                "source":   c.source,
                "path":     c.path,
                "default":  c.default,
            }
            for c in captures
        ]
