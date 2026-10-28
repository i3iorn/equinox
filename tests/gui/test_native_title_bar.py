"""Native title-bar setting — get/save round-trip."""

from __future__ import annotations

from PyQt6.QtCore import QSettings

from equinox.gui.theme import get_use_native_title_bar
from equinox.gui.theme import save_use_native_title_bar


class TestNativeTitleBar:
    """The native-title-bar preference persists through QSettings."""

    def test_default_is_false(self, isolated_gui_settings):
        assert get_use_native_title_bar() is False

    def test_round_trip(self, isolated_gui_settings):
        save_use_native_title_bar(True)
        assert get_use_native_title_bar() is True

        save_use_native_title_bar(False)
        assert get_use_native_title_bar() is False

    def test_writes_flush_to_settings_handle(self, isolated_gui_settings):
        """save_use_native_title_bar syncs so the next read sees it (issue #40)."""
        save_use_native_title_bar(True)
        handle = QSettings(isolated_gui_settings.fileName(), QSettings.Format.IniFormat)
        assert handle.value("window/native_title_bar", type=bool) is True
