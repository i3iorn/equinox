from __future__ import annotations

import re
from collections.abc import Iterable

from equinox.gui.syntax_highlighter.base import RegexHighlighterBase, RegexRule, _make_format
from equinox.gui.theme import Colors


class PythonHighlighter(RegexHighlighterBase):
    """Lightweight Python syntax highlighter using regex rules.

    Highlights keywords, strings, comments, numbers, and builtins.
    """

    def _build_rules(self) -> Iterable[RegexRule]:
        rules: list[RegexRule] = []

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
            ),
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
            ),
        )

        # Double-quoted strings
        str_fmt = _make_format(foreground=Colors.GREEN)
        rules.append(
            RegexRule(
                pattern=re.compile(r'"[^"\\]*(?:\\.[^"\\]*)*"'),
                fmt=str_fmt,
            ),
        )
        # Single-quoted strings
        rules.append(
            RegexRule(
                pattern=re.compile(r"'[^'\\]*(?:\\.[^'\\]*)*'"),
                fmt=str_fmt,
            ),
        )

        # Numbers
        num_fmt = _make_format(foreground=Colors.PURPLE)
        rules.append(
            RegexRule(
                pattern=re.compile(r"\b\d+\.?\d*\b"),
                fmt=num_fmt,
            ),
        )

        # Comments — must come last so they override other formats
        comment_fmt = _make_format(foreground=Colors.FG_MUTED, italic=True)
        rules.append(
            RegexRule(
                pattern=re.compile(r"#[^\n]*"),
                fmt=comment_fmt,
            ),
        )

        return rules
