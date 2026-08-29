"""Layout-robustness tests for the left sidebar.

The sidebar is narrow (300px by default, 180px minimum) but hosts six
navigation tabs and per-panel toolbars. Both had been laid out as though
space were unlimited, so Qt silently degraded them: toolbar labels elided
into unreadable stubs ("w Collecti"), and half the navigation tabs
disappeared behind small scroll arrows. These tests pin the fixes.
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

_APP = QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _themed():
    """Pin theme + font size, since both drive the widget widths measured here.

    The stylesheet supplies the padding, so without applying it the
    measurements reflect bare-Qt defaults rather than what a user sees. And
    the font size must be pinned to the default explicitly: other GUI tests
    (e.g. the PreferencesDialog ones) change it globally, which would
    otherwise silently inflate these widths depending on test order.
    """
    from equinox.gui.theme import DEFAULT_FONT_SIZE, apply_theme, get_font_size, set_font_size

    original = get_font_size()
    set_font_size(DEFAULT_FONT_SIZE)
    apply_theme(_APP)
    try:
        yield
    finally:
        set_font_size(original)


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("EQUINOX_DB_PATH", str(tmp_path / "test.db"))
    from equinox.storage import get_db

    return get_db()


@pytest.fixture()
def window(db):
    from equinox.gui.window import MainWindow

    win = MainWindow(db)
    yield win
    win.close()


@pytest.fixture()
def auth_dialog():
    from equinox.gui.dialogs.auth_dialog import AuthDialog

    dialog = AuthDialog(None)
    yield dialog
    dialog.close()


@pytest.fixture()
def collections_panel(db):
    from equinox.gui.collection_panel import CollectionsPanel

    panel = CollectionsPanel(db)
    yield panel
    panel.close()


@pytest.fixture()
def variables_panel(db):
    from equinox.gui.variables_panel import VariablesPanel

    panel = VariablesPanel(db)
    yield panel
    panel.close()


@pytest.fixture()
def history_panel(db):
    from equinox.gui.history_panel import HistoryPanel

    panel = HistoryPanel(db)
    yield panel
    panel.close()


@pytest.fixture()
def cookies_panel(db):
    from equinox.gui.cookies_panel import CookiesPanel

    panel = CookiesPanel(db)
    yield panel
    panel.close()


# -- Tab bars --------------------------------------------------------------
#
# Every QTabWidget narrow enough to overflow must elide rather than hide the
# overflow behind Qt's scroll arrows, which silently makes tabs unreachable.
# Both call sites go through ui_common.configure_tab_bar_elision, so these run
# against each one to keep the guarantee attached to the widget the user
# actually touches rather than to the helper alone.

_TAB_BAR_CASES = [
    pytest.param("window", "_left_tabs", id="sidebar"),
    pytest.param("auth_dialog", "tabs", id="auth-dialog"),
]


@pytest.mark.parametrize(("owner_fixture", "attr"), _TAB_BAR_CASES)
def test_tab_bar_scroll_buttons_disabled(request, owner_fixture, attr):
    """Scroll arrows hide navigation; eliding keeps every tab on screen."""
    tabs = getattr(request.getfixturevalue(owner_fixture), attr)
    bar = tabs.tabBar()
    assert bar is not None
    assert bar.usesScrollButtons() is False


@pytest.mark.parametrize(("owner_fixture", "attr"), _TAB_BAR_CASES)
def test_tab_bar_labels_elide_instead_of_overflowing(request, owner_fixture, attr):
    tabs = getattr(request.getfixturevalue(owner_fixture), attr)
    bar = tabs.tabBar()
    assert bar.elideMode() == Qt.TextElideMode.ElideRight
    # Expanding tabs would fight the elide and re-introduce overflow.
    assert bar.expanding() is False


@pytest.mark.parametrize(("owner_fixture", "attr"), _TAB_BAR_CASES)
def test_every_tab_has_a_nonempty_tooltip(request, owner_fixture, attr):
    """Elided labels are only acceptable if the full name is recoverable."""
    tabs = getattr(request.getfixturevalue(owner_fixture), attr)
    for index in range(tabs.count()):
        assert tabs.tabToolTip(index).strip(), f"tab {index} has no tooltip"


class TestSidebarTabBar:
    """Sidebar-specific tab-bar guarantees beyond the shared ones above."""

    def test_tooltips_name_the_tab_and_its_shortcut(self, window):
        from equinox.gui.window import _LEFT_TAB_LABELS

        assert window._left_tabs.count() == len(_LEFT_TAB_LABELS)
        for index, label in enumerate(_LEFT_TAB_LABELS):
            tip = window._left_tabs.tabToolTip(index)
            assert label in tip, f"tab {index} tooltip lost its name: {tip!r}"
            assert f"Alt+{index + 1}" in tip, f"tab {index} tooltip lost its shortcut: {tip!r}"

    def test_all_tabs_remain_clickable_at_minimum_sidebar_width(self, window):
        """At the narrowest allowed sidebar, every tab must still have a hit target."""
        window.resize(900, 700)
        window._left_tabs.setFixedWidth(180)  # _MIN_LEFT_W
        _APP.processEvents()

        bar = window._left_tabs.tabBar()
        for index in range(window._left_tabs.count()):
            rect = bar.tabRect(index)
            assert not rect.isEmpty(), f"tab {index} has no hit target at 180px"
            assert rect.width() > 0 and rect.height() > 0

    def test_activating_each_tab_by_shortcut_switches_to_it(self, window):
        for index in range(window._left_tabs.count()):
            window._activate_left_tab(index)
            _APP.processEvents()
            assert window._left_tabs.currentIndex() == index

    def test_tooltips_survive_lazy_panel_initialization(self, window):
        """Regression: lazy init swaps the tab via removeTab/insertTab.

        That discarded the tooltip at the exact moment the user first opened
        the tab so the elided label lost its only explanation precisely when
        it was needed.
        """
        from equinox.gui.window import _LEFT_TAB_LABELS

        for index in range(window._left_tabs.count()):
            window._ensure_tab_initialized(index)
            _APP.processEvents()

        for index, label in enumerate(_LEFT_TAB_LABELS):
            tip = window._left_tabs.tabToolTip(index)
            assert label in tip, f"tab {index} lost its tooltip after lazy init: {tip!r}"

    def test_variables_badge_does_not_leak_into_tooltip(self, window):
        """The Variables tab retitles itself with a session-var count badge."""
        window._ensure_tab_initialized(2)
        _APP.processEvents()
        window._left_tabs.setTabText(2, "Variables (3)")
        window.apply_left_tab_tooltip(2)

        assert window._left_tabs.tabToolTip(2) == "Variables  (Alt+3)"

    def test_out_of_range_tooltip_is_a_noop(self, window):
        """apply_left_tab_tooltip must stay total for indices with no label."""
        from equinox.gui.window import _LEFT_TAB_LABELS

        window.apply_left_tab_tooltip(len(_LEFT_TAB_LABELS) + 5)
        window.apply_left_tab_tooltip(-1)


# -- Panel toolbars --------------------------------------------------------
#
# Each sidebar panel toolbar shares one narrow (~300px) column, so its labels
# must be meaningfully shorter than the ones that used to overflow there, and
# whatever meaning they drop must reappear in a tooltip.
#
# Both thresholds below are deliberately single shared numbers rather than
# per-toolbar values. Tuning a threshold per panel until it passes records
# whatever the diff happened to produce instead of asserting an intent, and
# leaves no signal when a later edit creeps the labels back up. The measured
# ratios when these were written ranged 0.42-0.66, so 0.75 holds every
# toolbar to "at least a quarter narrower" with real headroom on each.

_MAX_LABEL_WIDTH_RATIO = 0.75
_MAX_LABEL_CHARS = 8


def _toolbar_widgets(panel, attrs):
    return [getattr(panel, name) for name in attrs]


# (panel fixture, widget attributes, the labels those widgets replaced)
_TOOLBAR_CASES = [
    pytest.param(
        "collections_panel",
        ("new_collection_btn", "import_btn", "refresh_btn", "auto_refresh_checkbox"),
        ("New Collection", "Import Openapi/Swagger", "Refresh", "Auto-refresh"),
        id="collections",
    ),
    pytest.param(
        "variables_panel",
        ("new_group_btn", "delete_group_btn"),
        ("New Group", "Delete Group"),
        id="variables-groups",
    ),
    pytest.param(
        "variables_panel",
        ("add_var_btn", "edit_var_btn", "remove_var_btn"),
        ("Add Variable", "Edit", "Remove"),
        id="variables-vars",
    ),
    pytest.param(
        "history_panel",
        ("refresh_btn", "clear_btn", "delete_sel_btn", "compare_btn", "cleanup_btn"),
        ("Refresh", "Clear All", "Delete Selected", "Compare 2 Selected", "Clean up..."),
        id="history",
    ),
    pytest.param(
        "cookies_panel",
        ("add_btn", "delete_btn", "clear_btn", "reveal_btn"),
        ("Add...", "Delete", "Clear All", "Reveal Values"),
        id="cookies",
    ),
]


@pytest.mark.parametrize(("panel_fixture", "attrs", "pre_fix_labels"), _TOOLBAR_CASES)
def test_toolbar_labels_are_far_narrower_than_the_labels_they_replaced(
    request,
    panel_fixture,
    attrs,
    pre_fix_labels,
):
    """The old labels overflowed the sidebar, so Qt elided them into stubs.

    Asserted as a ratio rather than an absolute pixel budget: button widths
    depend entirely on which fonts the host has (a machine with no font
    directory falls back to 'sans-serif' and inflates every metric by ~50%),
    so a fixed px threshold would pass or fail on environment rather than on
    the code under test. Measuring both label sets with the same
    QFontMetrics cancels that out.
    """
    from PyQt6.QtGui import QFontMetrics

    panel = request.getfixturevalue(panel_fixture)
    fm = QFontMetrics(panel.font())

    widgets = _toolbar_widgets(panel, attrs)
    new_text_px = sum(fm.horizontalAdvance(w.text()) for w in widgets)
    old_text_px = sum(fm.horizontalAdvance(t) for t in pre_fix_labels)

    assert new_text_px < old_text_px * _MAX_LABEL_WIDTH_RATIO, (
        f"toolbar label text is {new_text_px}px vs {old_text_px}px before the "
        f"fix (ratio {new_text_px / old_text_px:.2f}, limit "
        f"{_MAX_LABEL_WIDTH_RATIO}) - not a meaningful reduction; it will "
        "clip again in a narrow sidebar"
    )


@pytest.mark.parametrize(("panel_fixture", "attrs", "pre_fix_labels"), _TOOLBAR_CASES)
def test_no_toolbar_label_is_long_enough_to_elide(request, panel_fixture, attrs, pre_fix_labels):
    """Font-independent guard on the actual cause: over-long labels.

    Several controls share a ~300px sidebar, so each gets well under 100px.
    Anything beyond a short word will be elided by Qt.
    """
    panel = request.getfixturevalue(panel_fixture)
    for widget in _toolbar_widgets(panel, attrs):
        assert len(widget.text()) <= _MAX_LABEL_CHARS, (
            f"{widget.text()!r} is too long for a share of a 300px sidebar"
        )


@pytest.mark.parametrize(("panel_fixture", "attrs", "pre_fix_labels"), _TOOLBAR_CASES)
def test_every_toolbar_control_has_a_tooltip(request, panel_fixture, attrs, pre_fix_labels):
    """Short labels are only acceptable if the full meaning is recoverable."""
    panel = request.getfixturevalue(panel_fixture)
    for widget in _toolbar_widgets(panel, attrs):
        assert widget.toolTip().strip(), f"{widget.text()!r} has no tooltip"


def test_collections_import_button_explains_itself_when_disabled(collections_panel):
    """A parentless panel disables Import; it must say where to find it."""
    assert collections_panel.import_btn.isEnabled() is False
    assert "File" in collections_panel.import_btn.toolTip()


def test_history_toolbar_is_split_across_two_rows(history_panel):
    """Six controls never fit one ~300px row at any label length.

    Asserted structurally rather than by comparing shown widget y-positions:
    live geometry under the offscreen platform is timing-dependent (widths
    for identical code have ranged ~30px to ~600px across runs), which is
    why every other measurement in this module uses font metrics instead.
    """
    row1 = history_panel._toolbar_row1
    row2 = history_panel._toolbar_row2
    assert row1 is not row2

    def owning_row(widget):
        for row in (row1, row2):
            if row.indexOf(widget) != -1:
                return row
        raise AssertionError(f"{widget.text()!r} is in neither toolbar row")

    for widget in (history_panel.refresh_btn, history_panel.clear_btn):
        assert owning_row(widget) is row1

    for widget in (
        history_panel.delete_sel_btn,
        history_panel.compare_btn,
        history_panel.cleanup_btn,
    ):
        assert owning_row(widget) is row2, (
            f"{widget.text()!r} should be on the second row, not sharing row 1"
        )
