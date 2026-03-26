from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, List, Pattern

from PyQt6.QtGui import QSyntaxHighlighter, QTextCharFormat, QColor, QFont
from equinox.gui.theme import Colors


@dataclass(frozen=True)
class RegexRule:
    """Single regex + format rule used by regex-based highlighters."""

    pattern: Pattern[str]
    fmt: QTextCharFormat


def _make_format(
    *,
    foreground: str | None = None,
    bold: bool = False,
    italic: bool = False,
) -> QTextCharFormat:
    fmt = QTextCharFormat()
    if foreground is not None:
        fmt.setForeground(QColor(foreground))
    if bold:
        fmt.setFontWeight(QFont.Weight.Bold)
    if italic:
        fmt.setFontItalic(True)
    return fmt


def _variable_fmt() -> QTextCharFormat:
    """Shared format for {{variable}} placeholders — applied by all highlighters."""
    return _make_format(foreground=Colors.AMBER, bold=True)


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


class JsonHighlighter(RegexHighlighterBase):
    """Lightweight JSON syntax highlighter using regex rules.

    Highlights keys, strings, numbers, booleans, null, braces, and {{variables}}.
    """

    def _build_rules(self) -> Iterable[RegexRule]:
        rules: List[RegexRule] = []

        # JSON key (string followed by colon)
        key_fmt = _make_format(foreground=Colors.BLUE, bold=True)
        rules.append(
            RegexRule(
                pattern=re.compile(r'"([^"\\]|\\.)*"\s*(?=:)'),
                fmt=key_fmt,
            )
        )

        # String value
        str_fmt = _make_format(foreground=Colors.GREEN)
        rules.append(
            RegexRule(
                pattern=re.compile(r'"([^"\\]|\\.)*"'),
                fmt=str_fmt,
            )
        )

        # Number
        num_fmt = _make_format(foreground=Colors.PURPLE)
        rules.append(
            RegexRule(
                pattern=re.compile(
                    r"\b-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?\b"
                ),
                fmt=num_fmt,
            )
        )

        # Boolean / null
        kw_fmt = _make_format(foreground=Colors.AMBER, bold=True)
        rules.append(
            RegexRule(
                pattern=re.compile(r"\b(?:true|false|null)\b"),
                fmt=kw_fmt,
            )
        )

        # Braces / brackets
        brace_fmt = _make_format(foreground=Colors.FG_MUTED, bold=True)
        rules.append(
            RegexRule(
                pattern=re.compile(r"[{}\[\]]"),
                fmt=brace_fmt,
            )
        )

        return rules


class PythonHighlighter(RegexHighlighterBase):
    """Lightweight Python syntax highlighter using regex rules.

    Highlights keywords, strings, comments, numbers, and builtins.
    """

    def _build_rules(self) -> Iterable[RegexRule]:
        rules: List[RegexRule] = []

        # Keywords
        kw_fmt = _make_format(foreground=Colors.BLUE, bold=True)
        kw_pattern = (
            r"\b("
            r"if|else|elif|for|while|def|class|return|import|from|with|as|"
            r"try|except|finally|raise|pass|break|continue|in|not|and|or|"
            r"lambda|yield|True|False|None"
            r")\b"
        )
        rules.append(
            RegexRule(
                pattern=re.compile(kw_pattern),
                fmt=kw_fmt,
            )
        )

        # Built-in functions
        builtin_fmt = _make_format(foreground=Colors.AMBER)
        builtin_pattern = (
            r"\b("
            r"print|len|str|int|float|list|dict|set|tuple|range|enumerate|"
            r"zip|map|filter|type|isinstance|getattr|setattr|hasattr|repr|"
            r"vars|dir|abs|min|max|sum|any|all"
            r")\b"
        )
        rules.append(
            RegexRule(
                pattern=re.compile(builtin_pattern),
                fmt=builtin_fmt,
            )
        )

        # Double-quoted strings
        str_fmt = _make_format(foreground=Colors.GREEN)
        rules.append(
            RegexRule(
                pattern=re.compile(r'"[^"\\]*(?:\\.[^"\\]*)*"'),
                fmt=str_fmt,
            )
        )
        # Single-quoted strings
        rules.append(
            RegexRule(
                pattern=re.compile(r"'[^'\\]*(?:\\.[^'\\]*)*'"),
                fmt=str_fmt,
            )
        )

        # Numbers
        num_fmt = _make_format(foreground=Colors.PURPLE)
        rules.append(
            RegexRule(
                pattern=re.compile(r"\b\d+\.?\d*\b"),
                fmt=num_fmt,
            )
        )

        # Comments — must come last so they override other formats
        comment_fmt = _make_format(foreground=Colors.FG_MUTED, italic=True)
        rules.append(
            RegexRule(
                pattern=re.compile(r"#[^\n]*"),
                fmt=comment_fmt,
            )
        )

        return rules


class XmlHighlighter(RegexHighlighterBase):
    """Lightweight XML/HTML syntax highlighter.

    Highlights tags, attributes, attribute values, comments, and CDATA.
    """

    def _build_rules(self) -> Iterable[RegexRule]:
        rules: List[RegexRule] = []

        # XML comment  <!-- ... -->
        comment_fmt = _make_format(foreground=Colors.FG_MUTED, italic=True)
        rules.append(
            RegexRule(
                pattern=re.compile(r"<!--.*?-->", re.DOTALL),
                fmt=comment_fmt,
            )
        )

        # CDATA section
        cdata_fmt = _make_format(foreground=Colors.FG_MUTED)
        rules.append(
            RegexRule(
                pattern=re.compile(r"<!\[CDATA\[.*?\]\]>", re.DOTALL),
                fmt=cdata_fmt,
            )
        )

        # DOCTYPE / processing instruction
        pi_fmt = _make_format(foreground=Colors.PURPLE)
        rules.append(
            RegexRule(
                pattern=re.compile(r"<[?!][^>]*>"),
                fmt=pi_fmt,
            )
        )

        # Tag name  <tagName  or  </tagName
        tag_fmt = _make_format(foreground=Colors.BLUE, bold=True)
        rules.append(
            RegexRule(
                pattern=re.compile(r"</?[\w:-]+"),
                fmt=tag_fmt,
            )
        )
        rules.append(
            RegexRule(
                pattern=re.compile(r"/?>"),
                fmt=tag_fmt,
            )
        )

        # Attribute name
        attr_fmt = _make_format(foreground=Colors.AMBER)
        rules.append(
            RegexRule(
                pattern=re.compile(r"\b[\w:-]+="),
                fmt=attr_fmt,
            )
        )

        # Attribute value (double or single quoted)
        val_fmt = _make_format(foreground=Colors.GREEN)
        rules.append(
            RegexRule(
                pattern=re.compile(r'"[^"]*"'),
                fmt=val_fmt,
            )
        )
        rules.append(
            RegexRule(
                pattern=re.compile(r"'[^']*'"),
                fmt=val_fmt,
            )
        )

        return rules


# HTML uses the same grammar as XML.
HtmlHighlighter = XmlHighlighter


class YamlHighlighter(RegexHighlighterBase):
    """Lightweight YAML syntax highlighter.

    Highlights keys, strings, numbers, booleans, null, comments, and anchors.
    """

    def _build_rules(self) -> Iterable[RegexRule]:
        rules: List[RegexRule] = []

        # Comment
        comment_fmt = _make_format(foreground=Colors.FG_MUTED, italic=True)
        rules.append(
            RegexRule(
                pattern=re.compile(r"#[^\n]*"),
                fmt=comment_fmt,
            )
        )

        # Document separator (--- or ...)
        sep_fmt = _make_format(foreground=Colors.PURPLE, bold=True)
        rules.append(
            RegexRule(
                pattern=re.compile(r"^(---|\.\.\.)\s*$"),
                fmt=sep_fmt,
            )
        )

        # Anchor (&name) and alias (*name)
        anchor_fmt = _make_format(foreground=Colors.PURPLE)
        rules.append(
            RegexRule(
                pattern=re.compile(r"[&*][\w]+"),
                fmt=anchor_fmt,
            )
        )

        # Tag  !!type / !type
        tag_fmt = _make_format(foreground=Colors.PURPLE)
        rules.append(
            RegexRule(
                pattern=re.compile(r"![\w/]+"),
                fmt=tag_fmt,
            )
        )

        # Mapping key  key:
        key_fmt = _make_format(foreground=Colors.BLUE, bold=True)
        rules.append(
            RegexRule(
                pattern=re.compile(r"[\w.\-/]+(?=\s*:)"),
                fmt=key_fmt,
            )
        )

        # Quoted string values
        str_fmt = _make_format(foreground=Colors.GREEN)
        rules.append(
            RegexRule(
                pattern=re.compile(r'"[^"\\]*(?:\\.[^"\\]*)*"'),
                fmt=str_fmt,
            )
        )
        rules.append(
            RegexRule(
                pattern=re.compile(r"'[^']*'"),
                fmt=str_fmt,
            )
        )

        # Boolean / null
        kw_fmt = _make_format(foreground=Colors.AMBER, bold=True)
        rules.append(
            RegexRule(
                pattern=re.compile(
                    r"\b(?:true|false|yes|no|null|~|"
                    r"True|False|Yes|No|Null|NULL|TRUE|FALSE)\b"
                ),
                fmt=kw_fmt,
            )
        )

        # Number
        num_fmt = _make_format(foreground=Colors.PURPLE)
        rules.append(
            RegexRule(
                pattern=re.compile(
                    r"\b-?(?:0[xX][0-9a-fA-F]+|0[oO][0-7]+|"
                    r"[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?)\b"
                ),
                fmt=num_fmt,
            )
        )

        # List indicator
        list_fmt = _make_format(foreground=Colors.FG_MUTED, bold=True)
        rules.append(
            RegexRule(
                pattern=re.compile(r"^[ \t]*-(?= )"),
                fmt=list_fmt,
            )
        )

        return rules
