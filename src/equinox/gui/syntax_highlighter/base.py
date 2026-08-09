"""Base classes and utilities for syntax highlighters."""

from __future__ import annotations

import re
import weakref
from collections.abc import Iterable
from dataclasses import dataclass
from re import Pattern
from typing import ClassVar
from typing import Protocol

from equinox.gui.theme import Colors
from PyQt6.QtGui import QColor
from PyQt6.QtGui import QFont
from PyQt6.QtGui import QSyntaxHighlighter
from PyQt6.QtGui import QTextCharFormat
from PyQt6.QtWidgets import QWidget

# ---------------------------------------------------------------------------
# Format creation utilities
# ---------------------------------------------------------------------------


def _make_format(
    foreground: str,
    bold: bool = False,
    italic: bool = False,
    underline: bool = False,
) -> QTextCharFormat:
    """Return a QTextCharFormat configured with the given style.

    Parameters
    ----------
    foreground
        Color string from equinox.gui.theme.Colors.
    bold, italic, underline
        Text style flags.
    """
    fmt = QTextCharFormat()
    fmt.setForeground(QColor(foreground))
    if bold:
        fmt.setFontWeight(QFont.Weight.Bold)
    if italic:
        fmt.setFontItalic(True)
    if underline:
        fmt.setFontUnderline(True)
    return fmt


# ---------------------------------------------------------------------------
# Variable placeholder styling
# ---------------------------------------------------------------------------


def _variable_fmt() -> QTextCharFormat:
    """Build the ``{{variable}}`` placeholder format from the *current* theme.

    Must be evaluated fresh on every call, not cached as a module constant:
    ``Colors.AMBER`` reads live from the active palette, but a value
    captured once (e.g. at import time) would keep whichever theme was
    active at that moment for the rest of the process, even across
    later theme switches.
    """
    return _make_format(foreground=Colors.AMBER, bold=True)


#: Regex matching ``{{var}}`` tokens (Equinox interpolation syntax).
#: Compiled once at module load — reused by all highlighter instances.
#: (The pattern itself is theme-independent; only its format varies.)
_VARIABLE_PATTERN: Pattern[str] = re.compile(r"\{\{[\w.\-/: ]+\}\}")

#: Double-quoted string with backslash-escape support (``\"``, ``\\``, ...).
#: Shared by the Python and YAML highlighters, which use identical
#: escaping rules for double-quoted strings. Not used by the XML
#: highlighter, whose attribute values don't support backslash escapes at
#: all (XML uses ``&quot;`` entities instead) — a deliberately simpler,
#: different pattern there, not a duplicate of this one.
DOUBLE_QUOTED_STRING_RE: Pattern[str] = re.compile(r'"[^"\\]*(?:\\.[^"\\]*)*"')


# ---------------------------------------------------------------------------
# Theme-change propagation
# ---------------------------------------------------------------------------

#: Bumped by ``notify_theme_changed()`` so per-class rule caches know a
#: previously-cached build is stale and must be rebuilt from the new palette.
_theme_generation = 0


class _Refreshable(Protocol):
    def refresh_theme(self) -> None: ...


#: Weak references to every live, theme-aware highlighter instance
#: (``RegexHighlighterBase`` subclasses and ``JsonHighlighter``). Weak so
#: registering here never keeps a destroyed editor's highlighter alive.
_live_highlighters: weakref.WeakSet[_Refreshable] = weakref.WeakSet()


def register_highlighter(highlighter: _Refreshable) -> None:
    """Track *highlighter* so ``notify_theme_changed()`` can refresh it later."""
    _live_highlighters.add(highlighter)


def notify_theme_changed() -> None:
    """Rebuild rules/formats for every live highlighter and repaint it.

    Call after switching theme mode (see ``theme.manager.apply_theme``) so
    already-open request/response/script editors pick up the new palette
    instead of keeping whatever colors were active when they were
    constructed — highlighter rules and formats are otherwise built once
    and never re-read ``Colors.*`` again.
    """
    global _theme_generation
    _theme_generation += 1
    for highlighter in list(_live_highlighters):
        highlighter.refresh_theme()


# ---------------------------------------------------------------------------
# Rule definition
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RegexRule:
    """Single regex + format rule for a regex-based highlighter.

    Parameters
    ----------
    pattern
        Compiled regex pattern to match.
    fmt
        QTextCharFormat to apply to matches.
    """

    pattern: Pattern[str]
    fmt: QTextCharFormat


# ---------------------------------------------------------------------------
# Base highlighter
# ---------------------------------------------------------------------------


class RegexHighlighterBase(QSyntaxHighlighter):
    """Base class for simple regex-driven syntax highlighters.

    Subclasses implement ``_build_rules()`` to return a sequence of
    ``RegexRule`` instances. Variable placeholders ``{{var}}`` are
    highlighted last (via ``_VARIABLE_PATTERN`` and ``_variable_fmt()``)
    so they always override language-specific formats.

    The default implementation highlights only variables (when no language
    rules are provided), making it suitable for plain-text contexts.

    Rule lists are cached per subclass, invalidated only when the theme
    actually changes (see ``notify_theme_changed()``) — a fresh highlighter
    instance is constructed for every response body rendered (see
    ``response_panel/display_mixin.py``), and under a stable theme all of
    them share one compiled rule list instead of each re-running
    ``_build_rules()`` (which recompiles ~10 regexes) from scratch.
    """

    #: Per-subclass cache of (theme_generation, rules) — shared by every
    #: instance of that subclass, not just this one.
    _rule_cache: ClassVar[dict[type, tuple[int, list[RegexRule]]]] = {}

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._load_rules()
        register_highlighter(self)

    def _load_rules(self) -> None:
        """Populate ``self._rules``, reusing the cached build when still fresh."""
        cls = type(self)
        cached = RegexHighlighterBase._rule_cache.get(cls)
        if cached is None or cached[0] != _theme_generation:
            cached = (_theme_generation, list(self._build_rules()))
            RegexHighlighterBase._rule_cache[cls] = cached
        self._rules = cached[1]

    def _build_rules(self) -> Iterable[RegexRule]:
        """Return the language-specific highlighting rules.

        Override in subclasses. The default yields nothing, allowing
        ``RegexHighlighterBase`` to be used as a variable-only highlighter.
        """
        return []

    def refresh_theme(self) -> None:
        """Rebuild rules for the current theme and repaint. See ``notify_theme_changed()``."""
        self._load_rules()
        self.rehighlight()

    def highlightBlock(self, text: str | None) -> None:
        text = text or ""
        # Apply language-specific rules.
        for rule in self._rules:
            for match in rule.pattern.finditer(text):
                self.setFormat(match.start(), match.end() - match.start(), rule.fmt)

        # Apply {{variable}} placeholders last so they override other formats.
        # Skip the regex scan when no placeholder can be present — this method
        # is called on every keystroke for every visible block, so the O(n)
        # string-scan short-circuit has a meaningful effect on large documents.
        if "{{" not in text:
            return

        fmt = _variable_fmt()
        for match in _VARIABLE_PATTERN.finditer(text):
            self.setFormat(match.start(), match.end() - match.start(), fmt)
