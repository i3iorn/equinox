"""UI density toggle — get/save round-trip and derived scale factors."""

from __future__ import annotations

import pytest
from PyQt6.QtCore import QSettings

from equinox.gui.theme import DENSITY_COMFORTABLE
from equinox.gui.theme import DENSITY_COMPACT
from equinox.gui.theme import DENSITY_MODES
from equinox.gui.theme import DENSITY_SPACIOUS
from equinox.gui.theme import get_density_font_scale
from equinox.gui.theme import get_density_padding_scale
from equinox.gui.theme import get_ui_density
from equinox.gui.theme import save_ui_density


class TestUIDensity:
    """Density persists through QSettings and derives correct scale factors."""

    def test_default_is_comfortable(self, isolated_gui_settings):
        assert get_ui_density() == DENSITY_COMFORTABLE

    def test_round_trip_for_each_mode(self, isolated_gui_settings):
        for mode in DENSITY_MODES:
            save_ui_density(mode)
            assert get_ui_density() == mode

    def test_invalid_value_falls_back_to_comfortable(self, isolated_gui_settings):
        settings = QSettings(isolated_gui_settings.fileName(), QSettings.Format.IniFormat)
        settings.setValue("appearance/ui_density", "not-a-mode")
        settings.sync()
        assert get_ui_density() == DENSITY_COMFORTABLE

    def test_padding_scale_relative_to_comfortable(self, isolated_gui_settings):
        save_ui_density(DENSITY_COMPACT)
        assert get_density_padding_scale() == pytest.approx(0.75)

        save_ui_density(DENSITY_COMFORTABLE)
        assert get_density_padding_scale() == pytest.approx(1.0)

        save_ui_density(DENSITY_SPACIOUS)
        assert get_density_padding_scale() == pytest.approx(1.35)

    def test_font_scale_relative_to_comfortable(self, isolated_gui_settings):
        save_ui_density(DENSITY_COMPACT)
        assert get_density_font_scale() == pytest.approx(0.9)

        save_ui_density(DENSITY_COMFORTABLE)
        assert get_density_font_scale() == pytest.approx(1.0)

        save_ui_density(DENSITY_SPACIOUS)
        assert get_density_font_scale() == pytest.approx(1.1)
