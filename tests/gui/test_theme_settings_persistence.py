"""Font-size and theme-mode writes must be readable immediately.

`_settings()` used to build a fresh QSettings on every call, and the save
helpers never synced. A value written through one instance could still be
unflushed when the next instance read it, so `set_font_size(n)` followed by
`get_font_size()` could hand back the previous value. That is what made the
zoom controls intermittently look like they did nothing, and it failed
test_zoom_in/test_zoom_out with `assert 9 == 9 + 1` under some orderings.
"""

from __future__ import annotations

import pytest


@pytest.fixture()
def restore_appearance(tmp_path, monkeypatch):
    """Point GUI settings at a throwaway INI file for the duration of the test.

    Deliberately not the real store. QSettings' native backend is the user's
    registry on Windows, so asserting against it both mutates the developer's
    own appearance settings and makes the test depend on whatever a previous
    run left behind -- which is how an earlier version of this file passed
    locally and then failed inside the pre-push hook, reading back the
    default instead of the value it had just written.
    """
    from PyQt6.QtCore import QSettings

    import equinox.gui.ui_common as ui_common

    handle = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    monkeypatch.setattr(ui_common, "_settings_handle", handle)
    yield handle
    ui_common.reset_gui_settings_handle()


def test_font_size_is_readable_immediately_after_writing(restore_appearance):
    from equinox.gui.theme import MAX_FONT_SIZE, MIN_FONT_SIZE, get_font_size, set_font_size

    for size in (MIN_FONT_SIZE, MIN_FONT_SIZE + 1, MAX_FONT_SIZE):
        set_font_size(size)
        assert get_font_size() == size, f"write of {size} was not visible to the next read"


def test_repeated_increments_each_take_effect(restore_appearance):
    """Zoom re-reads the value to compute the next step, so writes must land."""
    from equinox.gui.theme import MIN_FONT_SIZE, get_font_size, set_font_size

    set_font_size(MIN_FONT_SIZE)
    for expected in range(MIN_FONT_SIZE + 1, MIN_FONT_SIZE + 5):
        set_font_size(get_font_size() + 1)
        assert get_font_size() == expected


def test_theme_mode_is_readable_immediately_after_writing(restore_appearance):
    from equinox.gui.theme import get_theme_mode, set_theme_mode

    for mode in ("dark", "light"):
        set_theme_mode(mode)
        assert get_theme_mode() == mode


def test_theme_and_ui_common_share_one_settings_handle(restore_appearance):
    """Two handles on the same store is how the writes went missing."""
    from equinox.gui.theme.settings import _settings
    from equinox.gui.ui_common import get_gui_settings

    assert _settings() is get_gui_settings()
    assert _settings() is restore_appearance
