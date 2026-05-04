from PyQt6.QtWidgets import QApplication

from equinox.gui import theme


def test_dialog_text_selectors_present_for_light_and_dark_modes():
    app = QApplication.instance() or QApplication([])

    original_mode = theme.get_theme_mode()
    try:
        for mode in (theme.THEME_LIGHT, theme.THEME_DARK, theme.THEME_OCEANIC):
            theme.set_theme_mode(mode)
            stylesheet = app.styleSheet()

            assert "QWidget {" in stylesheet
            assert "QLabel, QCheckBox, QRadioButton, QGroupBox::title" in stylesheet
            assert "QMessageBox QLabel" in stylesheet
            assert "QAbstractItemView {" in stylesheet
            assert "selection-color:" in stylesheet
    finally:
        theme.set_theme_mode(original_mode)

