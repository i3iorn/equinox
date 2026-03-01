"""Syntax highlighting for QTextEdit widgets — JSON, Python, XML, HTML, YAML."""

import re
from PyQt6.QtGui import QSyntaxHighlighter, QTextCharFormat, QColor, QFont
from equinox.gui.theme import Colors

# Shared format for {{variable}} placeholders — applied by all highlighters.
def _variable_fmt() -> QTextCharFormat:
    fmt = QTextCharFormat()
    fmt.setForeground(QColor(Colors.AMBER))
    fmt.setFontWeight(QFont.Weight.Bold)
    return fmt

_VARIABLE_PATTERN = re.compile(r'\{\{[\w.\-/: ]+\}\}')


class JsonHighlighter(QSyntaxHighlighter):
    """Lightweight JSON syntax highlighter using regex rules.

    Highlights keys, strings, numbers, booleans, null, braces, and {{variables}}.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rules = self._build_rules()
        self._var_fmt = _variable_fmt()

    def _build_rules(self):
        rules = []

        # JSON key (string followed by colon)
        key_fmt = QTextCharFormat()
        key_fmt.setForeground(QColor(Colors.BLUE))
        key_fmt.setFontWeight(QFont.Weight.Bold)
        rules.append((re.compile(r'"([^"\\]|\\.)*"\s*(?=:)'), key_fmt))

        # String value
        str_fmt = QTextCharFormat()
        str_fmt.setForeground(QColor(Colors.GREEN))
        rules.append((re.compile(r'"([^"\\]|\\.)*"'), str_fmt))

        # Number
        num_fmt = QTextCharFormat()
        num_fmt.setForeground(QColor(Colors.PURPLE))
        rules.append((re.compile(r'\b-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?\b'), num_fmt))

        # Boolean / null
        kw_fmt = QTextCharFormat()
        kw_fmt.setForeground(QColor(Colors.AMBER))
        kw_fmt.setFontWeight(QFont.Weight.Bold)
        rules.append((re.compile(r'\b(?:true|false|null)\b'), kw_fmt))

        # Braces / brackets
        brace_fmt = QTextCharFormat()
        brace_fmt.setForeground(QColor(Colors.FG_MUTED))
        brace_fmt.setFontWeight(QFont.Weight.Bold)
        rules.append((re.compile(r'[{}\[\]]'), brace_fmt))

        return rules

    def highlightBlock(self, text: str) -> None:
        for pattern, fmt in self._rules:
            for match in pattern.finditer(text):
                start = match.start()
                length = match.end() - start
                self.setFormat(start, length, fmt)
        # {{variable}} placeholders — applied last so they override other formats
        for match in _VARIABLE_PATTERN.finditer(text):
            self.setFormat(match.start(), match.end() - match.start(), self._var_fmt)


class PythonHighlighter(QSyntaxHighlighter):
    """Lightweight Python syntax highlighter using regex rules.

    Highlights keywords, strings, comments, numbers, and builtins.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rules = self._build_rules()
        self._var_fmt = _variable_fmt()

    def _build_rules(self):
        rules = []

        # Keywords
        kw_fmt = QTextCharFormat()
        kw_fmt.setForeground(QColor(Colors.BLUE))
        kw_fmt.setFontWeight(QFont.Weight.Bold)
        kw_pattern = (
            r"\b(if|else|elif|for|while|def|class|return|import|from|with|as|"
            r"try|except|finally|raise|pass|break|continue|in|not|and|or|"
            r"lambda|yield|True|False|None)\b"
        )
        rules.append((re.compile(kw_pattern), kw_fmt))

        # Built-in functions
        builtin_fmt = QTextCharFormat()
        builtin_fmt.setForeground(QColor(Colors.AMBER))
        builtin_pattern = (
            r"\b(print|len|str|int|float|list|dict|set|tuple|range|enumerate|"
            r"zip|map|filter|type|isinstance|getattr|setattr|hasattr|repr|"
            r"vars|dir|abs|min|max|sum|any|all)\b"
        )
        rules.append((re.compile(builtin_pattern), builtin_fmt))

        # Double-quoted strings
        str_fmt = QTextCharFormat()
        str_fmt.setForeground(QColor(Colors.GREEN))
        rules.append((re.compile(r'"[^"\\]*(?:\\.[^"\\]*)*"'), str_fmt))
        # Single-quoted strings
        rules.append((re.compile(r"'[^'\\]*(?:\\.[^'\\]*)*'"), str_fmt))

        # Numbers
        num_fmt = QTextCharFormat()
        num_fmt.setForeground(QColor(Colors.PURPLE))
        rules.append((re.compile(r"\b\d+\.?\d*\b"), num_fmt))

        # Comments — must come last so they override other formats
        comment_fmt = QTextCharFormat()
        comment_fmt.setForeground(QColor(Colors.FG_MUTED))
        comment_fmt.setFontItalic(True)
        rules.append((re.compile(r"#[^\n]*"), comment_fmt))

        return rules

    def highlightBlock(self, text: str) -> None:
        for pattern, fmt in self._rules:
            for match in pattern.finditer(text):
                start = match.start()
                length = match.end() - start
                self.setFormat(start, length, fmt)
        for match in _VARIABLE_PATTERN.finditer(text):
            self.setFormat(match.start(), match.end() - match.start(), self._var_fmt)


class XmlHighlighter(QSyntaxHighlighter):
    """Lightweight XML/HTML syntax highlighter.

    Highlights tags, attributes, attribute values, comments, and CDATA.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rules = self._build_rules()
        self._var_fmt = _variable_fmt()

    def _build_rules(self):
        rules = []

        # XML comment  <!-- ... -->
        comment_fmt = QTextCharFormat()
        comment_fmt.setForeground(QColor(Colors.FG_MUTED))
        comment_fmt.setFontItalic(True)
        rules.append((re.compile(r'<!--.*?-->', re.DOTALL), comment_fmt))

        # CDATA section
        cdata_fmt = QTextCharFormat()
        cdata_fmt.setForeground(QColor(Colors.FG_MUTED))
        rules.append((re.compile(r'<!\[CDATA\[.*?\]\]>', re.DOTALL), cdata_fmt))

        # DOCTYPE / processing instruction
        pi_fmt = QTextCharFormat()
        pi_fmt.setForeground(QColor(Colors.PURPLE))
        rules.append((re.compile(r'<[?!][^>]*>'), pi_fmt))

        # Tag name  <tagName  or  </tagName
        tag_fmt = QTextCharFormat()
        tag_fmt.setForeground(QColor(Colors.BLUE))
        tag_fmt.setFontWeight(QFont.Weight.Bold)
        rules.append((re.compile(r'</?[\w:-]+'), tag_fmt))
        rules.append((re.compile(r'/?>'), tag_fmt))

        # Attribute name
        attr_fmt = QTextCharFormat()
        attr_fmt.setForeground(QColor(Colors.AMBER))
        rules.append((re.compile(r'\b[\w:-]+='), attr_fmt))

        # Attribute value (double or single quoted)
        val_fmt = QTextCharFormat()
        val_fmt.setForeground(QColor(Colors.GREEN))
        rules.append((re.compile(r'"[^"]*"'), val_fmt))
        rules.append((re.compile(r"'[^']*'"), val_fmt))

        return rules

    def highlightBlock(self, text: str) -> None:
        for pattern, fmt in self._rules:
            for match in pattern.finditer(text):
                self.setFormat(match.start(), match.end() - match.start(), fmt)
        for match in _VARIABLE_PATTERN.finditer(text):
            self.setFormat(match.start(), match.end() - match.start(), self._var_fmt)


# HTML uses the same grammar as XML.
HtmlHighlighter = XmlHighlighter


class YamlHighlighter(QSyntaxHighlighter):
    """Lightweight YAML syntax highlighter.

    Highlights keys, strings, numbers, booleans, null, comments, and anchors.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rules = self._build_rules()
        self._var_fmt = _variable_fmt()

    def _build_rules(self):
        rules = []

        # Comment
        comment_fmt = QTextCharFormat()
        comment_fmt.setForeground(QColor(Colors.FG_MUTED))
        comment_fmt.setFontItalic(True)
        rules.append((re.compile(r'#[^\n]*'), comment_fmt))

        # Document separator (--- or ...)
        sep_fmt = QTextCharFormat()
        sep_fmt.setForeground(QColor(Colors.PURPLE))
        sep_fmt.setFontWeight(QFont.Weight.Bold)
        rules.append((re.compile(r'^(---|\.\.\.)\s*$'), sep_fmt))

        # Anchor (&name) and alias (*name)
        anchor_fmt = QTextCharFormat()
        anchor_fmt.setForeground(QColor(Colors.PURPLE))
        rules.append((re.compile(r'[&*][\w]+'), anchor_fmt))

        # Tag  !!type
        tag_fmt = QTextCharFormat()
        tag_fmt.setForeground(QColor(Colors.PURPLE))
        rules.append((re.compile(r'![\w/]+'), tag_fmt))

        # Mapping key  key:
        key_fmt = QTextCharFormat()
        key_fmt.setForeground(QColor(Colors.BLUE))
        key_fmt.setFontWeight(QFont.Weight.Bold)
        rules.append((re.compile(r'[\w.\-/]+(?=\s*:)'), key_fmt))

        # Quoted string values
        str_fmt = QTextCharFormat()
        str_fmt.setForeground(QColor(Colors.GREEN))
        rules.append((re.compile(r'"[^"\\]*(?:\\.[^"\\]*)*"'), str_fmt))
        rules.append((re.compile(r"'[^']*'"), str_fmt))

        # Boolean / null
        kw_fmt = QTextCharFormat()
        kw_fmt.setForeground(QColor(Colors.AMBER))
        kw_fmt.setFontWeight(QFont.Weight.Bold)
        rules.append((re.compile(r'\b(?:true|false|yes|no|null|~|True|False|Yes|No|Null|NULL|TRUE|FALSE)\b'), kw_fmt))

        # Number
        num_fmt = QTextCharFormat()
        num_fmt.setForeground(QColor(Colors.PURPLE))
        rules.append((re.compile(r'\b-?(?:0[xX][0-9a-fA-F]+|0[oO][0-7]+|[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?)\b'), num_fmt))

        # List indicator
        list_fmt = QTextCharFormat()
        list_fmt.setForeground(QColor(Colors.FG_MUTED))
        list_fmt.setFontWeight(QFont.Weight.Bold)
        rules.append((re.compile(r'^[ \t]*-(?= )'), list_fmt))

        return rules

    def highlightBlock(self, text: str) -> None:
        for pattern, fmt in self._rules:
            for match in pattern.finditer(text):
                self.setFormat(match.start(), match.end() - match.start(), fmt)
        for match in _VARIABLE_PATTERN.finditer(text):
            self.setFormat(match.start(), match.end() - match.start(), self._var_fmt)
