from __future__ import annotations

import re
from dataclasses import dataclass
from re import Pattern
from typing import Iterable, List, Optional

from PyQt6.QtGui import QTextCharFormat, QColor, QFont, QSyntaxHighlighter

from equinox.gui.theme import Colors


def _make_format(
    foreground: str,
    bold: bool = False,
    italic: bool = False,
    underline: bool = False,
) -> QTextCharFormat:
    """Return a :class:`QTextCharFormat` configured with the given style."""
    fmt = QTextCharFormat()
    fmt.setForeground(QColor(foreground))
    if bold:
        fmt.setFontWeight(QFont.Weight.Bold)
    if italic:
        fmt.setFontItalic(True)
    if underline:
        fmt.setFontUnderline(True)
    return fmt


def _variable_fmt() -> QTextCharFormat:
    """Shared format for {{variable}} placeholders — applied by all highlighters."""
    return _make_format(foreground=Colors.AMBER, bold=True)


@dataclass(frozen=True)
class RegexRule:
    """Single regex + format rule used by regex-based highlighters."""

    pattern: Pattern[str]
    fmt: QTextCharFormat
    group: Optional[str] = None  # reserved for future named-group extraction


_VARIABLE_PATTERN: Pattern[str] = re.compile(r"\{\{[\w.\-/: ]+\}\}")


class RegexHighlighterBase(QSyntaxHighlighter):
    """Base class for simple regex-driven syntax highlighters.

    Subclasses implement ``_build_rules`` to return a sequence of
    :class:`RegexRule` instances.  Variable placeholders ``{{var}}`` are
    highlighted last so they always override language-specific formats.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._rules: List[RegexRule] = list(self._build_rules())
        self._var_fmt: QTextCharFormat = _variable_fmt()

    # Subclasses override this
    def _build_rules(self) -> Iterable[RegexRule]:
        """Return the language-specific highlighting rules.

        Override in subclasses.  The default yields nothing, so
        ``RegexHighlighterBase`` can be used as a variable-only highlighter
        when no additional rules are needed.
        """
        return []

    def highlightBlock(self, text: str) -> None:  # type: ignore[override]
        # Apply language-specific rules.
        for rule in self._rules:
            for match in rule.pattern.finditer(text):
                self.setFormat(match.start(), match.end() - match.start(), rule.fmt)

        # Apply {{variable}} placeholders last so they override other formats.
        # Skip the regex entirely when the block cannot contain a placeholder —
        # this is called on every keystroke for every visible block, so the
        # O(n) string-scan short-circuit has a meaningful effect on large files.
        if "{{" not in text:
            return
        for match in _VARIABLE_PATTERN.finditer(text):
            self.setFormat(match.start(), match.end() - match.start(), self._var_fmt)
