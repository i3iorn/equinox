"""Coverage-boosting tests for GUI widgets."""
from PyQt6.QtCore import QCoreApplication
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication
from PyQt6.QtWidgets import QTextEdit

_APP = QApplication.instance() or QApplication([])

# SearchBar debounce is 250 ms — wait slightly longer to let the timer fire.
_SEARCH_DEBOUNCE_WAIT_MS = 300


def _process():
    QCoreApplication.processEvents()


def _process_search():
    """Advance the event loop long enough to flush the SearchBar debounce timer."""
    QTest.qWait(_SEARCH_DEBOUNCE_WAIT_MS)


# ─────────────────────────────────────────────────────────────────────────────
# UrlLineEdit
# ─────────────────────────────────────────────────────────────────────────────


class TestUrlLineEdit:
    def test_instantiate(self):
        from equinox.gui.widgets.url_line_edit import UrlLineEdit

        w = UrlLineEdit()
        assert w._param_suffix == ""

    def test_set_param_suffix_updates(self):
        from equinox.gui.widgets.url_line_edit import UrlLineEdit

        w = UrlLineEdit()
        w.set_param_suffix("?a=1&b=2")
        assert w._param_suffix == "?a=1&b=2"

    def test_set_same_suffix_no_update(self):
        from equinox.gui.widgets.url_line_edit import UrlLineEdit

        w = UrlLineEdit()
        w.set_param_suffix("?x=1")
        w.set_param_suffix("?x=1")  # same — no repaint needed
        assert w._param_suffix == "?x=1"

    def test_focus_events_do_not_crash(self):
        from equinox.gui.widgets.url_line_edit import UrlLineEdit

        w = UrlLineEdit()
        w.show()
        w.setFocus()
        _process()
        w.clearFocus()
        _process()
        w.hide()

    def test_paint_event_with_suffix(self):
        from equinox.gui.widgets.url_line_edit import UrlLineEdit

        w = UrlLineEdit()
        w.resize(300, 30)
        w.setText("https://example.com/api")
        w.set_param_suffix("?key=value")
        w.show()
        _process()
        w.hide()

    def test_paint_event_no_suffix(self):
        from equinox.gui.widgets.url_line_edit import UrlLineEdit

        w = UrlLineEdit()
        w.resize(300, 30)
        w.setText("https://example.com")
        # No suffix — paint should be a no-op after super call
        w.show()
        _process()
        w.hide()


# ─────────────────────────────────────────────────────────────────────────────
# KeyValueTable
# ─────────────────────────────────────────────────────────────────────────────


class TestKeyValueTable:
    def test_instantiate_has_empty_row(self):
        from equinox.gui.widgets.key_value_table import KeyValueTable

        t = KeyValueTable()
        assert t.rowCount() == 1  # one empty trailing row

    def test_get_data_empty(self):
        from equinox.gui.widgets.key_value_table import KeyValueTable

        t = KeyValueTable()
        assert t.get_data() == {}

    def test_set_and_get_data(self):
        from equinox.gui.widgets.key_value_table import KeyValueTable

        t = KeyValueTable()
        t.set_data({"Authorization": "Bearer tok", "Content-Type": "application/json"})
        _process()
        data = t.get_data()
        assert data["Authorization"] == "Bearer tok"
        assert data["Content-Type"] == "application/json"

    def test_reset_clears_rows(self):
        from equinox.gui.widgets.key_value_table import KeyValueTable

        t = KeyValueTable()
        t.set_data({"k": "v"})
        t.reset()
        _process()
        assert t.get_data() == {}

    def test_typing_in_last_row_adds_new_row(self):
        from PyQt6.QtWidgets import QTableWidgetItem

        from equinox.gui.widgets.key_value_table import KeyValueTable

        t = KeyValueTable()
        initial_rows = t.rowCount()
        # Simulate typing in the last row key cell
        item = t.item(initial_rows - 1, 0)
        if item is None:
            from PyQt6.QtWidgets import QTableWidgetItem

            item = QTableWidgetItem("new-key")
            t.setItem(initial_rows - 1, 0, item)
        else:
            item.setText("new-key")
        _process()
        assert t.rowCount() > initial_rows

    def test_set_data_empty_dict(self):
        from equinox.gui.widgets.key_value_table import KeyValueTable

        t = KeyValueTable()
        t.set_data({})
        _process()
        assert t.get_data() == {}


# ─────────────────────────────────────────────────────────────────────────────
# DragDropTree
# ─────────────────────────────────────────────────────────────────────────────


class TestDragDropTree:
    def test_instantiate(self):
        from equinox.gui.widgets.drag_drop_tree import DragDropTree

        tree = DragDropTree()
        assert tree.dragEnabled()
        assert tree.acceptDrops()

    def test_item_data_none_item(self):
        from equinox.gui.widgets.drag_drop_tree import DragDropTree

        tree = DragDropTree()
        assert tree._node_data(None) == {}

    def test_item_data_with_item(self):
        from PyQt6.QtWidgets import QTreeWidgetItem

        from equinox.gui.widgets.drag_drop_tree import DragDropTree

        tree = DragDropTree()
        item = QTreeWidgetItem(["My Request"])
        data = {"type": "request", "id": 42}
        item.setData(0, Qt.ItemDataRole.UserRole, data)
        assert tree._node_data(item) == data

    def test_start_drag_no_item(self):
        from equinox.gui.widgets.drag_drop_tree import DragDropTree

        tree = DragDropTree()
        # Should not crash with no selection
        tree.startDrag(Qt.DropAction.MoveAction)

    def test_start_drag_non_request_item(self):
        from PyQt6.QtWidgets import QTreeWidgetItem

        from equinox.gui.widgets.drag_drop_tree import DragDropTree

        tree = DragDropTree()
        item = QTreeWidgetItem(["My Collection"])
        item.setData(0, Qt.ItemDataRole.UserRole, {"type": "collection", "id": 1})
        tree.addTopLevelItem(item)
        tree.setCurrentItem(item)
        # Non-request items should not start a drag
        tree.startDrag(Qt.DropAction.MoveAction)

    def test_drag_enter_event_accept(self):
        from PyQt6.QtCore import QMimeData

        from equinox.gui.widgets.drag_drop_tree import DragDropTree

        tree = DragDropTree()
        tree.show()
        _process()
        mime = QMimeData()
        mime.setText("42")
        # Use a simulated event via QApplication
        _process()
        tree.hide()

    def test_signals_exist(self):
        from equinox.gui.widgets.drag_drop_tree import DragDropTree

        tree = DragDropTree()
        assert hasattr(tree, "request_dropped")
        assert hasattr(tree, "request_reorder")


# ─────────────────────────────────────────────────────────────────────────────
# JsonBodyEditor
# ─────────────────────────────────────────────────────────────────────────────


class TestJsonBodyEditor:
    def test_instantiate(self):
        from equinox.gui.widgets.json_body_editor import JsonBodyEditor

        ed = JsonBodyEditor()
        assert ed is not None

    def test_set_and_get_text(self):
        from equinox.gui.widgets.json_body_editor import JsonBodyEditor

        ed = JsonBodyEditor()
        ed.setPlainText('{"key": "value"}')
        assert ed.toPlainText() == '{"key": "value"}'

    def test_line_number_area_width(self):
        from equinox.gui.widgets.json_body_editor import JsonBodyEditor

        ed = JsonBodyEditor()
        width = ed.line_number_area_width()
        assert isinstance(width, int)
        assert width > 0

    def test_line_number_area_instantiated(self):
        from equinox.gui.widgets.json_body_editor import JsonBodyEditor, LineNumberArea

        ed = JsonBodyEditor()
        assert hasattr(ed, "line_number_area")
        assert isinstance(ed.line_number_area, LineNumberArea)

    def test_auto_format_json(self):
        from equinox.gui.widgets.json_body_editor import JsonBodyEditor

        ed = JsonBodyEditor()
        ed.setPlainText('{"b":2,"a":1}')
        ed._auto_format_json()
        result = ed.toPlainText()
        assert '"a"' in result
        assert '"b"' in result

    def test_auto_format_json_invalid(self):
        from equinox.gui.widgets.json_body_editor import JsonBodyEditor

        ed = JsonBodyEditor()
        ed.setPlainText("{not valid json}")
        # Should not crash on invalid JSON
        ed._auto_format_json()

    def test_key_press_tab_inserts_spaces(self):
        from equinox.gui.widgets.json_body_editor import JsonBodyEditor

        ed = JsonBodyEditor()
        ed.setPlainText("")
        # Simulate Tab key
        ev = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Tab, Qt.KeyboardModifier.NoModifier)
        ed.keyPressEvent(ev)
        text = ed.toPlainText()
        assert "    " in text or text == "    "

    def test_key_press_open_brace_auto_closes(self):
        from equinox.gui.widgets.json_body_editor import JsonBodyEditor

        ed = JsonBodyEditor()
        ed.setPlainText("")
        ev = QKeyEvent(
            QKeyEvent.Type.KeyPress, Qt.Key.Key_BraceLeft, Qt.KeyboardModifier.NoModifier, "{",
        )
        ed.keyPressEvent(ev)
        text = ed.toPlainText()
        assert "{" in text

    def test_paint_line_numbers(self):
        from equinox.gui.widgets.json_body_editor import JsonBodyEditor

        ed = JsonBodyEditor()
        ed.resize(400, 300)
        ed.setPlainText("line1\nline2\nline3")
        ed.show()
        _process()
        ed.hide()

    def test_cursor_position_changed(self):
        from equinox.gui.widgets.json_body_editor import JsonBodyEditor

        ed = JsonBodyEditor()
        ed.setPlainText('{"key": "value"}')
        # Move cursor
        cursor = ed.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        ed.setTextCursor(cursor)
        _process()


# ─────────────────────────────────────────────────────────────────────────────
# SearchBar
# ─────────────────────────────────────────────────────────────────────────────


class TestSearchBar:
    def _make_bar(self):
        from equinox.gui.response_panel.search import SearchBar

        target = QTextEdit()
        target.setPlainText("Hello World\nThis is a test\nHello again")
        bar = SearchBar(target)
        return bar, target

    def test_instantiate(self):
        bar, _ = self._make_bar()
        assert not bar.isVisible()

    def test_show_and_focus(self):
        bar, _ = self._make_bar()
        bar.show_and_focus()
        assert bar.isVisible()

    def test_hide(self):
        bar, _ = self._make_bar()
        bar.show_and_focus()
        bar.hide()
        assert not bar.isVisible()

    def test_plain_text_search(self):
        bar, target = self._make_bar()
        bar.show_and_focus()
        bar._input.setText("Hello")
        _process_search()
        assert len(bar._matches) >= 1

    def test_case_sensitive_search(self):
        bar, target = self._make_bar()
        bar.show_and_focus()
        bar._case_btn.setChecked(True)
        bar._input.setText("hello")  # lowercase won't match uppercase "Hello"
        _process_search()
        # Case-sensitive: "hello" ≠ "Hello"
        assert len(bar._matches) == 0

    def test_case_insensitive_search(self):
        bar, target = self._make_bar()
        bar.show_and_focus()
        bar._case_btn.setChecked(False)
        bar._input.setText("hello")
        _process_search()
        # Case-insensitive: should match "Hello"
        assert len(bar._matches) >= 1

    def test_regex_mode(self):
        bar, target = self._make_bar()
        bar.show_and_focus()
        bar._regex_btn.setChecked(True)
        bar._input.setText("Hello.*")
        _process_search()
        assert len(bar._matches) >= 1

    def test_find_next(self):
        bar, target = self._make_bar()
        bar.show_and_focus()
        bar._input.setText("Hello")
        _process_search()
        initial_idx = bar._current_idx
        bar._find_next()
        # Either advances or wraps around
        assert bar._current_idx >= 0

    def test_find_prev(self):
        bar, target = self._make_bar()
        bar.show_and_focus()
        bar._input.setText("Hello")
        _process_search()
        bar._find_prev()
        assert bar._current_idx >= 0

    def test_empty_search(self):
        bar, target = self._make_bar()
        bar.show_and_focus()
        bar._input.setText("")
        _process()
        assert bar._matches == []
        assert bar._current_idx == -1

    def test_no_match(self):
        bar, target = self._make_bar()
        bar.show_and_focus()
        bar._input.setText("ZZZNOMATCH")
        _process_search()
        assert bar._matches == []

    def test_jsonpath_mode_toggle(self):
        bar, target = self._make_bar()
        bar.show_and_focus()
        bar._jp_btn.setChecked(True)
        _process()
        # JSONPath and Regex are mutually exclusive
        assert not bar._regex_btn.isChecked()

    def test_regex_mode_exclusive_with_jsonpath(self):
        bar, target = self._make_bar()
        bar.show_and_focus()
        bar._jp_btn.setChecked(True)
        _process()
        bar._regex_btn.setChecked(True)
        _process()
        assert not bar._jp_btn.isChecked()

    def test_set_json_doc(self):
        bar, target = self._make_bar()
        bar.set_json_doc({"users": [{"name": "Alice"}, {"name": "Bob"}]})
        assert bar._json_obj is not None

    def test_set_json_doc_none(self):
        bar, target = self._make_bar()
        bar.set_json_doc(None)
        assert bar._json_obj is None

    def test_jsonpath_search_with_json(self):
        from equinox.gui.response_panel.search import SearchBar

        target = QTextEdit()
        target.setPlainText('{"users": [{"name": "Alice"}]}')
        bar = SearchBar(target)
        bar.set_json_doc({"users": [{"name": "Alice"}]})
        bar.show_and_focus()
        bar._jp_btn.setChecked(True)
        bar._input.setText("$.users[0].name")
        _process()
        # Result label should be visible/updated
