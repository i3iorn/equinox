from __future__ import annotations

import re
from typing import Iterable, List

from equinox.gui.syntax_highlighter.base import RegexHighlighterBase, RegexRule, _make_format
from equinox.gui.theme import Colors


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


HtmlHighlighter = XmlHighlighter
