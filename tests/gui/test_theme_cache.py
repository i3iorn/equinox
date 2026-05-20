from PyQt6.QtWidgets import QApplication

from equinox.gui import theme


def test_stylesheet_cache_isolated_per_dark_variant():
    app = QApplication.instance() or QApplication([])

    original_mode = theme.get_theme_mode()
    try:
        theme._ss_cache.clear()

        theme.set_theme_mode(theme.THEME_DARK)
        dark_stylesheet = app.styleSheet()

        theme.set_theme_mode(theme.THEME_MUTED_DARK)
        muted_dark_stylesheet = app.styleSheet()

        theme.set_theme_mode(theme.THEME_OCEANIC)
        oceanic_stylesheet = app.styleSheet()

        assert dark_stylesheet != muted_dark_stylesheet
        assert dark_stylesheet != oceanic_stylesheet
        assert muted_dark_stylesheet != oceanic_stylesheet

        # Cache should keep one entry per resolved dark palette at this font size.
        assert (theme.THEME_DARK, theme.get_font_size()) in theme._ss_cache
        assert (theme.THEME_MUTED_DARK, theme.get_font_size()) in theme._ss_cache
        assert (theme.THEME_OCEANIC, theme.get_font_size()) in theme._ss_cache
    finally:
        theme.set_theme_mode(original_mode)
