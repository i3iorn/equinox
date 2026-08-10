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


class TestSidebarTabBar:
    """All six sidebar destinations must stay reachable at any width."""

    def test_scroll_buttons_disabled(self, window):
        """Scroll arrows hide navigation; eliding keeps every tab on screen."""
        bar = window._left_tabs.tabBar()
        assert bar is not None
        assert bar.usesScrollButtons() is False

    def test_labels_elide_instead_of_overflowing(self, window):
        bar = window._left_tabs.tabBar()
        assert bar.elideMode() == Qt.TextElideMode.ElideRight
        # Expanding tabs would fight the elide and re-introduce overflow.
        assert bar.expanding() is False

    def test_every_tab_has_a_tooltip_naming_it_and_its_shortcut(self, window):
        """Elided labels are only acceptable if the full name is recoverable."""
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
        the tab — so the elided label lost its only explanation precisely when
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


class TestCollectionsToolbarFitsSidebar:
    """The collections toolbar must fit the sidebar without eliding labels."""

    def _toolbar_buttons(self, panel):
        return [
            panel.new_collection_btn,
            panel.import_btn,
            panel.refresh_btn,
            panel.auto_refresh_checkbox,
        ]

    # The labels this toolbar used before the fix. Kept here so the test can
    # compare old vs new using the *same* font metrics.
    _PRE_FIX_LABELS = ("New Collection", "Import Openapi/Swagger", "Refresh", "Auto-refresh")

    def test_toolbar_is_far_narrower_than_the_labels_it_replaced(self, qapp_collections_panel):
        """The old labels needed ~445px against a 300px sidebar, so Qt elided
        them into unreadable stubs ("w Collecti", "Openapi/S").

        Asserted as a ratio rather than an absolute pixel budget: button
        widths depend entirely on which fonts the host has (a machine with no
        font directory falls back to 'sans-serif' and inflates every metric by
        ~50%), so a fixed px threshold would pass or fail on environment
        rather than on the code under test. Measuring both label sets with the
        same QFontMetrics cancels that out.
        """
        from PyQt6.QtGui import QFontMetrics

        panel = qapp_collections_panel
        buttons = self._toolbar_buttons(panel)
        fm = QFontMetrics(panel.font())

        new_text_px = sum(fm.horizontalAdvance(w.text()) for w in buttons)
        old_text_px = sum(fm.horizontalAdvance(t) for t in self._PRE_FIX_LABELS)

        assert new_text_px < old_text_px * 0.6, (
            f"toolbar label text is {new_text_px}px vs {old_text_px}px before the fix — "
            "not a meaningful reduction; it will elide again in a 300px sidebar"
        )

    def test_no_toolbar_label_is_long_enough_to_elide(self, qapp_collections_panel):
        """Font-independent guard on the actual cause: over-long labels.

        Four controls share a ~300px sidebar, so each gets ~70px. Anything
        beyond a short word will be elided by Qt.
        """
        for widget in self._toolbar_buttons(qapp_collections_panel):
            assert len(widget.text()) <= 8, (
                f"{widget.text()!r} is too long for a quarter of a 300px sidebar"
            )

    def test_every_toolbar_control_has_a_tooltip(self, qapp_collections_panel):
        """Short labels are only acceptable if the full meaning is recoverable."""
        for widget in self._toolbar_buttons(qapp_collections_panel):
            assert widget.toolTip().strip(), f"{widget.text()!r} has no tooltip"

    def test_import_button_explains_itself_when_disabled(self, db):
        """A parentless panel disables Import; it must say where to find it."""
        from equinox.gui.collection_panel import CollectionsPanel

        panel = CollectionsPanel(db)
        try:
            assert panel.import_btn.isEnabled() is False
            assert "File" in panel.import_btn.toolTip()
        finally:
            panel.close()


@pytest.fixture()
def qapp_collections_panel(db):
    from equinox.gui.collection_panel import CollectionsPanel

    panel = CollectionsPanel(db)
    yield panel
    panel.close()


class TestVariablesPanelFitsSidebar:
    """The Groups/Variables split toolbars must fit a 300px sidebar tab.

    Each toolbar gets roughly half of an already-narrow sidebar, so its
    buttons must be meaningfully shorter than the labels that used to
    overflow there. Measured as a font-metric ratio rather than a live
    layout pixel count: actual widget geometry under the offscreen platform
    is sensitive to show()/processEvents timing (real widths observed for
    the same code ranged from ~30px to ~600px across runs), which the
    Collections toolbar test below already works around the same way.
    """

    def _groups_buttons(self, panel):
        return [panel.new_group_btn, panel.delete_group_btn]

    def _variables_buttons(self, panel):
        return [panel.add_var_btn, panel.edit_var_btn, panel.remove_var_btn]

    _PRE_FIX_GROUPS_LABELS = ("New Group", "Delete Group")
    _PRE_FIX_VARIABLES_LABELS = ("Add Variable", "Edit", "Remove")

    def test_toolbar_labels_are_far_narrower_than_the_labels_they_replaced(
        self,
        qapp_variables_panel,
    ):
        from PyQt6.QtGui import QFontMetrics

        panel = qapp_variables_panel
        fm = QFontMetrics(panel.font())

        for buttons, pre_fix_labels in (
            (self._groups_buttons(panel), self._PRE_FIX_GROUPS_LABELS),
            (self._variables_buttons(panel), self._PRE_FIX_VARIABLES_LABELS),
        ):
            new_text_px = sum(fm.horizontalAdvance(w.text()) for w in buttons)
            old_text_px = sum(fm.horizontalAdvance(t) for t in pre_fix_labels)
            assert new_text_px < old_text_px * 0.75, (
                f"toolbar label text is {new_text_px}px vs {old_text_px}px before "
                "the fix — not a meaningful reduction; it will clip again in a "
                "narrow sidebar column"
            )

    def test_every_toolbar_button_has_a_tooltip(self, qapp_variables_panel):
        """Short labels are only acceptable if the full meaning is recoverable."""
        panel = qapp_variables_panel
        for widget in self._groups_buttons(panel) + self._variables_buttons(panel):
            assert widget.toolTip().strip(), f"{widget.text()!r} has no tooltip"


@pytest.fixture()
def qapp_variables_panel(db):
    from equinox.gui.variables_panel import VariablesPanel

    panel = VariablesPanel(db)
    yield panel
    panel.close()
