from __future__ import annotations

import re
from dataclasses import dataclass
from re import Pattern
from typing import List, Iterable

from PyQt6.QtGui import QTextCharFormat, QColor, QFont, QSyntaxHighlighter

from equinox.gui.theme import Colors


def _variable_fmt() -> QTextCharFormat:
    """Shared format for {{variable}} placeholders — applied by all highlighters."""
    return _make_format(foreground=Colors.AMBER, bold=True)


def _make_format(foreground: str, bold=False, italic=False, underline=False):
    fmt = QTextCharFormat()
    fmt.setForeground(QColor(foreground))

    if bold:
        fmt.setFontWeight(QFont.Weight.Bold)
    if italic:
        fmt.setFontItalic(True)
    if underline:
        fmt.setFontUnderline(True)

    return fmt


@dataclass(frozen=True)
class RegexRule:
    """Single regex + format rule used by regex-based highlighters."""

    pattern: Pattern[str]
    fmt: QTextCharFormat
    group: str | None = None


_VARIABLE_PATTERN: Pattern[str] = re.compile(r"\{\{[\w.\-/: ]+\}\}")


class RegexHighlighterBase(QSyntaxHighlighter):
    """Base class for simple regex-driven syntax highlighters.

    Subclasses implement `_build_rules` to return a sequence of RegexRule
    instances. Variable placeholders `{{var}}` are highlighted last so they
    override other formats.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._rules: List[RegexRule] = list(self._build_rules())
        self._var_fmt: QTextCharFormat = _variable_fmt()

    # Subclasses override this
    def _build_rules(self) -> Iterable[RegexRule]:
        return []

    def highlightBlock(self, text: str) -> None:  # type: ignore[override]
        # Apply language-specific rules
        for rule in self._rules:
            for match in rule.pattern.finditer(text):
                start = match.start()
                length = match.end() - start
                self.setFormat(start, length, rule.fmt)

        # Apply {{variable}} placeholders last so they override other formats
        for match in _VARIABLE_PATTERN.finditer(text):
            start = match.start()
            length = match.end() - start
            self.setFormat(start, length, self._var_fmt)
