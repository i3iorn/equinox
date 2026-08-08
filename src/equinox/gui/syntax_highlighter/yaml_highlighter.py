from __future__ import annotations

import re
from collections.abc import Iterable

from equinox.gui.syntax_highlighter.base import RegexHighlighterBase, RegexRule, _make_format
from equinox.gui.theme import Colors


class YamlHighlighter(RegexHighlighterBase):
    """Lightweight YAML syntax highlighter.

    Highlights keys, strings, numbers, booleans, null, comments, and anchors.
    """

    def _build_rules(self) -> Iterable[RegexRule]:
        rules: list[RegexRule] = []

        # Comment
        comment_fmt = _make_format(foreground=Colors.FG_MUTED, italic=True)
        rules.append(
            RegexRule(
                pattern=re.compile(r"#[^\n]*"),
                fmt=comment_fmt,
            ),
        )

        # Document separator (--- or ...)
        sep_fmt = _make_format(foreground=Colors.PURPLE, bold=True)
        rules.append(
            RegexRule(
                pattern=re.compile(r"^(---|\.\.\.)\s*$"),
                fmt=sep_fmt,
            ),
        )

        # Anchor (&name) and alias (*name)
        anchor_fmt = _make_format(foreground=Colors.PURPLE)
        rules.append(
            RegexRule(
                pattern=re.compile(r"[&*][\w]+"),
                fmt=anchor_fmt,
            ),
        )

        # Tag  !!type / !type
        tag_fmt = _make_format(foreground=Colors.PURPLE)
        rules.append(
            RegexRule(
                pattern=re.compile(r"![\w/]+"),
                fmt=tag_fmt,
            ),
        )

        # Mapping key  key:
        key_fmt = _make_format(foreground=Colors.BLUE, bold=True)
        rules.append(
            RegexRule(
                pattern=re.compile(r"[\w.\-/]+(?=\s*:)"),
                fmt=key_fmt,
            ),
        )

        # Quoted string values
        str_fmt = _make_format(foreground=Colors.GREEN)
        rules.append(
            RegexRule(
                pattern=re.compile(r'"[^"\\]*(?:\\.[^"\\]*)*"'),
                fmt=str_fmt,
            ),
        )
        rules.append(
            RegexRule(
                pattern=re.compile(r"'[^']*'"),
                fmt=str_fmt,
            ),
        )

        # Boolean / null
        kw_fmt = _make_format(foreground=Colors.AMBER, bold=True)
        rules.append(
            RegexRule(
                pattern=re.compile(
                    r"\b(?:true|false|yes|no|null|~|" r"True|False|Yes|No|Null|NULL|TRUE|FALSE)\b",
                ),
                fmt=kw_fmt,
            ),
        )

        # Number
        num_fmt = _make_format(foreground=Colors.PURPLE)
        rules.append(
            RegexRule(
                pattern=re.compile(
                    r"\b-?(?:0[xX][0-9a-fA-F]+|0[oO][0-7]+|"
                    r"[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?)\b",
                ),
                fmt=num_fmt,
            ),
        )

        # List indicator
        list_fmt = _make_format(foreground=Colors.FG_MUTED, bold=True)
        rules.append(
            RegexRule(
                pattern=re.compile(r"^[ \t]*-(?= )"),
                fmt=list_fmt,
            ),
        )

        return rules
