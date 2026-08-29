"""Regression tests for the regex-based syntax highlighters."""

from PyQt6.QtGui import QColor, QTextDocument


def _format_at(doc: QTextDocument, pos: int):
    """Return the resolved QTextCharFormat covering *pos* in the first block."""
    block = doc.findBlockByNumber(0)
    layout = block.layout()
    for fmt_range in layout.formats():
        if fmt_range.start <= pos < fmt_range.start + fmt_range.length:
            return fmt_range.format
    return None


def test_python_highlighter_string_wins_over_comment_hash():
    """A '#' inside a string literal (e.g. a URL fragment) must keep the
    string's color, not be recolored as a comment — regression test for a
    rule-ordering bug where the comment rule always won on overlap."""
    from equinox.gui.syntax_highlighter.python_highlighter import PythonHighlighter
    from equinox.gui.theme import Colors

    text = 'url = "https://x.com/page#frag"'
    doc = QTextDocument()
    doc.setPlainText(text)
    highlighter = PythonHighlighter(doc)
    highlighter.rehighlight()

    hash_pos = text.index("#")
    fmt = _format_at(doc, hash_pos)
    assert fmt is not None
    assert fmt.foreground().color() == QColor(Colors.GREEN)


def test_theme_change_refreshes_already_open_regex_highlighter():
    """Switching theme must re-color an already-open editor's highlighter,
    not just future ones — regression test for rules/formats that were
    only ever built once at construction time."""
    from equinox.gui.syntax_highlighter.base import notify_theme_changed
    from equinox.gui.syntax_highlighter.python_highlighter import PythonHighlighter
    from equinox.gui.theme import palettes

    original_palette = dict(palettes.get_active_palette())
    try:
        text = "if True:\n    pass"
        doc = QTextDocument()
        doc.setPlainText(text)
        highlighter = PythonHighlighter(doc)
        highlighter.rehighlight()

        if_pos = text.index("if")
        fmt_before = _format_at(doc, if_pos)
        assert fmt_before is not None

        new_palette = dict(original_palette)
        new_palette["BLUE"] = "#123456"
        palettes.set_active_palette(new_palette)
        notify_theme_changed()

        fmt_after = _format_at(doc, if_pos)
        assert fmt_after is not None
        assert fmt_after.foreground().color() == QColor("#123456")
    finally:
        palettes.set_active_palette(original_palette)
        notify_theme_changed()


def test_theme_change_refreshes_already_open_json_highlighter():
    """Same guarantee for JsonHighlighter, which builds its format map
    separately from RegexHighlighterBase (formats.py had its own frozen
    module-level color dict)."""
    from equinox.gui.syntax_highlighter.json_highlighter.highlighter import JsonHighlighter
    from equinox.gui.theme import palettes

    original_palette = dict(palettes.get_active_palette())
    try:
        text = '{"a": "b"}'
        doc = QTextDocument()
        doc.setPlainText(text)
        highlighter = JsonHighlighter(doc)
        highlighter.rehighlight()

        # "a" is a KEY (followed by ':'), so target the "b" VALUE string,
        # which uses the STRING/GREEN format.
        str_pos = text.index('"b"') + 1
        fmt_before = _format_at(doc, str_pos)
        assert fmt_before is not None

        new_palette = dict(original_palette)
        new_palette["GREEN"] = "#123456"
        palettes.set_active_palette(new_palette)
        from equinox.gui.syntax_highlighter.base import notify_theme_changed

        notify_theme_changed()

        fmt_after = _format_at(doc, str_pos)
        assert fmt_after is not None
        assert fmt_after.foreground().color() == QColor("#123456")
    finally:
        palettes.set_active_palette(original_palette)
        from equinox.gui.syntax_highlighter.base import notify_theme_changed

        notify_theme_changed()


def test_python_highlighter_still_colors_real_comments():
    """A genuine comment (not inside a string) must still be highlighted."""
    from equinox.gui.syntax_highlighter.python_highlighter import PythonHighlighter
    from equinox.gui.theme import Colors

    text = "x = 1  # a real comment"
    doc = QTextDocument()
    doc.setPlainText(text)
    highlighter = PythonHighlighter(doc)
    highlighter.rehighlight()

    hash_pos = text.index("#")
    fmt = _format_at(doc, hash_pos)
    assert fmt is not None
    assert fmt.foreground().color() == QColor(Colors.FG_MUTED)
    assert fmt.fontItalic() is True
