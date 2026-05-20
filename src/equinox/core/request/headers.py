"""RFC 7230-compliant HTTP header container."""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Mapping
from typing import Any, cast

from equinox.core.exceptions import ValidationError

__all__ = ["HeaderDict"]

# RFC 7230 §3.2.6 — header field names are tokens
# token = 1*tchar
# tchar = "!" / "#" / "$" / "%" / "&" / "'" / "*" / "+" / "-" / "." /
#         "^" / "_" / "`" / "|" / "~" / DIGIT / ALPHA
_HEADER_NAME_RE: re.Pattern = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")


class HeaderDict(dict):
    """Dictionary for HTTP headers with RFC-compliant case-insensitive lookup.

    Behavior:
    - Field names are compared and stored in lower-case internally.
    - Iteration yields the most-recently-set original-cased key names.
    - Values are coerced to ``str``; ``None`` becomes ``""``.
    - CR/LF validation is intentionally deferred to send-time (via
      :class:`~equinox.core.validation.Validator`) so that ``Request`` objects
      can be constructed and edited freely before they are sent.
    - Subclasses ``dict`` so ``isinstance(h, dict)`` keeps working for callers
      that haven't been updated to the new type.
    """

    def __init__(self, data: dict[str, Any] | None = None) -> None:
        super().__init__()
        # Tracks lower-case name → original-case name for display/export.
        self._orig: dict[str, str] = {}
        if data:
            self.update(data)

    # ── Validation ────────────────────────────────────────────────────────────

    @staticmethod
    def _validate_name(name: str) -> None:
        if not isinstance(name, str) or not _HEADER_NAME_RE.fullmatch(name):
            raise ValidationError(f"Invalid header name: {name!r}")

    @staticmethod
    def _coerce_value(value: Any) -> str:
        """Coerce *value* to string; ``None`` → empty string."""
        return "" if value is None else str(value)

    # ── Core dict protocol ────────────────────────────────────────────────────

    def __setitem__(self, key: str, value: Any) -> None:
        self._validate_name(key)
        lower = key.lower()
        self._orig[lower] = key
        super().__setitem__(lower, self._coerce_value(value))

    def __getitem__(self, key: str) -> str:
        return super().__getitem__(key.lower())

    def __delitem__(self, key: str) -> None:
        lower = key.lower()
        super().__delitem__(lower)
        self._orig.pop(lower, None)

    def __contains__(self, key: object) -> bool:
        return isinstance(key, str) and super().__contains__(key.lower())

    def __iter__(self) -> Iterator[str]:
        return self.keys()

    # ── Overrides that honour original case ───────────────────────────────────

    def get(self, key: str, default: Any = None) -> Any:  # type: ignore[override]
        return super().get(key.lower(), default)

    def keys(self) -> Iterator[str]:  # type: ignore[override]
        for lower in super().keys():
            yield self._orig.get(lower, lower)

    def items(self) -> Iterator[tuple[str, str]]:  # type: ignore[override]
        for lower, value in super().items():
            yield self._orig.get(lower, lower), value

    def update(self, other: dict[str, Any] | None = None, **kwargs: Any) -> None:  # type: ignore[override]
        if other is not None:
            it = other.items() if isinstance(other, dict) else other
            for k, v in it:
                self[k] = v
        for k, v in kwargs.items():
            self[k] = v

    # ── Equality ──────────────────────────────────────────────────────────────

    def __eq__(self, other: object) -> bool:
        """Case-insensitive comparison with any dict-like object."""
        try:
            if isinstance(other, Mapping):
                other_items = other.items()
            else:
                other_dict = dict(cast(Iterable[tuple[Any, Any]], other))
                other_items = other_dict.items()

            other_lower = {str(k).lower(): v for k, v in other_items}
            return dict(super().items()) == other_lower
        except Exception:
            return False

    # ── Serialisation helper ──────────────────────────────────────────────────

    def as_canonical_dict(self, *, lowercase: bool = True) -> dict[str, str]:
        """Return a plain ``dict`` suitable for serialisation or export.

        Args:
            lowercase: When ``True`` (default) keys are lower-cased, which is
                       useful for storage and search.  Pass ``False`` to get
                       original-case keys for display or HTTP export.
        """
        if lowercase:
            return dict(super().items())
        return {k: v for k, v in self.items()}

    def __repr__(self) -> str:  # pragma: no cover
        return f"HeaderDict({dict(self.items())!r})"
