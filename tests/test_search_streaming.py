import pytest

# Skip this test if pytest-qt (the qtbot fixture) is not available in the
# test environment. Running GUI tests requires the pytest-qt plugin.
pytest.importorskip("pytestqt")

from PyQt6.QtWidgets import QTextEdit

from equinox.gui.response_panel.search_bar import SearchBar


def test_search_streaming_partial_results(qtbot):
    """Start a streaming search on a large document and ensure partial
    results arrive and UI remains responsive.
    """
    # Build a large document with repeating tokens
    lines = [f'line {i}: VALUE_{i % 100}' for i in range(20000)]
    big = "\n".join(lines)

    editor = QTextEdit()
    editor.setPlainText(big)
    qtbot.addWidget(editor)

    sb = SearchBar(editor)
    qtbot.addWidget(sb)

    # Start a background search directly (bypass debounce timer)
    sb._start_search_job("VALUE_42")

    # Wait until at least one partial batch has been applied
    qtbot.waitUntil(lambda: hasattr(sb, '_offsets') and len(sb._offsets) > 0, timeout=3000)

    assert len(sb._offsets) > 0

    # Cancel and ensure the UI reflects cancellation
    sb._on_cancel_search()
    assert sb._match_label.text() == 'cancelled'

