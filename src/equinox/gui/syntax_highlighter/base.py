"""Base classes and utilities for syntax highlighters."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from re import Pattern

from PyQt6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat

from equinox.gui.theme import Colors

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

#: Shared format for {{variable}} placeholders across all highlighters.
#: Applied after language-specific rules so variables always override.
_VARIABLE_FMT: QTextCharFormat = _make_format(foreground=Colors.AMBER, bold=True)

#: Regex matching ``{{var}}`` tokens (Equinox interpolation syntax).
#: Compiled once at module load — reused by all highlighter instances.
_VARIABLE_PATTERN: Pattern[str] = re.compile(r"\{\{[\w.\-/: ]+\}\}")


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
    highlighted last (via ``_VARIABLE_PATTERN`` and ``_VARIABLE_FMT``)
    so they always override language-specific formats.

    The default implementation highlights only variables (when no language
    rules are provided), making it suitable for plain-text contexts.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        # Compile rules once at construction time; subclass implementations
        # are expected to be cheap (just returning a list).
        self._rules: list[RegexRule] = list(self._build_rules())

    def _build_rules(self) -> Iterable[RegexRule]:
        """Return the language-specific highlighting rules.

        Override in subclasses. The default yields nothing, allowing
        ``RegexHighlighterBase`` to be used as a variable-only highlighter.
        """
        return []

    def highlightBlock(self, text: str) -> None:  # noqa: N802
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

        for match in _VARIABLE_PATTERN.finditer(text):
            self.setFormat(match.start(), match.end() - match.start(), _VARIABLE_FMT)
