"""Stylesheet generation from resolved palette values."""

from __future__ import annotations

from .settings import get_small_text_size


def build_stylesheet(base_pt: int, colors: dict[str, str]) -> str:
    """Generate the application-wide stylesheet string."""
    c = colors
    sm = get_small_text_size(base_pt)

    return f"""
    /* -- Global -- */
    * {{
        font-size: {base_pt}pt;
    }}
    QMainWindow, QDialog, QMessageBox, QInputDialog {{
        background: {c["BG"]};
        color: {c["FG"]};
    }}
    QWidget {{
        color: {c["FG"]};
    }}
    QLabel, QCheckBox, QRadioButton, QGroupBox::title {{
        color: {c["FG"]};
        background: transparent;
    }}
    QLabel:disabled, QCheckBox:disabled, QRadioButton:disabled {{
        color: {c["FG_SUBTLE"]};
    }}

    /* -- Buttons -- */
    QPushButton {{
        padding: 4px 10px;
        border: 1px solid {c["BORDER"]};
        border-radius: 4px;
        background: {c["BG"]};
        color: {c["FG"]};
        min-height: 1.5em;
    }}
    QToolButton {{
        padding: 2px 4px;
        border: 1px solid {c["BORDER"]};
        border-radius: 4px;
        background: {c["BG"]};
        color: {c["FG"]};
        min-height: 1.5em;
    }}
    QToolButton[popupMode="1"], QToolButton[popupMode="2"] {{
        padding-right: 18px;
    }}
    QPushButton:hover, QToolButton:hover {{
        background: {c["BG_ALT"]};
        border-color: {c["FG_MUTED"]};
    }}
    QPushButton:pressed, QToolButton:pressed {{
        background: {c["BORDER"]};
    }}
    QPushButton:disabled, QToolButton:disabled {{
        color: {c["FG_SUBTLE"]};
        border-color: {c["BORDER"]};
    }}
    QPushButton#sendBtn {{
        background: {c["BLUE"]};
        color: #ffffff;
        border: 1px solid {c["BLUE"]};
        font-weight: bold;
    }}
    QPushButton#sendBtn:hover {{
        background: {c["SEND_HOVER"]};
        border-color: {c["SEND_HOVER"]};
    }}
    QPushButton#sendBtn:disabled {{
        background: {c["FG_SUBTLE"]};
        border-color: {c["FG_SUBTLE"]};
        color: {c["BG"]};
    }}
    QPushButton#cancelBtn {{
        background: {c["RED"]};
        color: #ffffff;
        border: 1px solid {c["RED"]};
    }}

    /* -- Inputs -- */
    QLineEdit, QTextEdit, QPlainTextEdit {{
        border: 1px solid {c["BORDER"]};
        border-radius: 4px;
        padding: 4px 6px;
        background: {c["BG"]};
        color: {c["FG"]};
        selection-background-color: {c["SELECTION"]};
        selection-color: {c["FG"]};
    }}
    QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {{
        border-color: {c["BORDER_FCS"]};
    }}
    QComboBox {{
        border: 1px solid {c["BORDER"]};
        border-radius: 4px;
        padding: 3px 8px;
        background: {c["BG"]};
        color: {c["FG"]};
        min-height: 24px;
    }}
    QComboBox:focus {{
        border-color: {c["BORDER_FCS"]};
    }}
    QComboBox::drop-down {{
        border: none;
        width: 20px;
    }}
    QComboBox QAbstractItemView {{
        background: {c["BG"]};
        color: {c["FG"]};
        border: 1px solid {c["BORDER"]};
        selection-background-color: {c["SELECTION"]};
        selection-color: {c["FG"]};
    }}
    QAbstractItemView {{
        background: {c["BG"]};
        color: {c["FG"]};
        alternate-background-color: {c["BG_ALT"]};
        selection-background-color: {c["SELECTION"]};
        selection-color: {c["FG"]};
    }}

    QWidget#field-valid {{
        border-color: {c["GREEN"]} !important;
    }}
    QWidget#field-error {{
        border-color: {c["RED"]} !important;
    }}

    /* -- Tabs -- */
    QTabWidget::pane {{
        border: 1px solid {c["BORDER"]};
        background: {c["BG"]};
        border-radius: 4px;
    }}
    QTabBar::tab {{
        padding: 8px 16px;
        border: 1px solid transparent;
        margin-right: 4px;
        color: {c["FG_MUTED"]};
        font-size: {int(sm * 1.2)}pt;
        background: transparent;
        border-radius: 4px;
    }}
    QTabBar::tab:selected {{
        color: {c["BLUE"]};
        background: {c["BG"]};
        border: 1px solid {c["BORDER"]};
    }}
    QTabBar[tabPosition="0"]::tab:selected {{
        border-bottom: 2px solid {c["BLUE"]};
    }}
    QTabBar[tabPosition="1"]::tab:selected {{
        border-top: 2px solid {c["BLUE"]};
    }}
    QTabBar::tab:hover:!selected {{
        color: {c["FG"]};
        background: {c["BG_ALT"]};
    }}

    /* -- Sidebar -- */
    QWidget#sidebar {{
        background: {c["BG_ALT"]};
        border-right: 1px solid {c["BORDER"]};
        min-width: 50px;
        max-width: 50px;
    }}
    QToolButton#sidebarBtn {{
        border: none;
        border-radius: 0px;
        background: transparent;
        padding: 10px;
        min-height: 40px;
    }}
    QToolButton#sidebarBtn:hover {{
        background: {c["SELECTION"]};
    }}
    QToolButton#sidebarBtn:checked {{
        background: {c["BG"]};
        border-left: 3px solid {c["BLUE"]};
    }}

    /* -- Tables / trees / lists -- */
    QTableWidget, QTableView {{
        border: 1px solid {c["BORDER"]};
        gridline-color: {c["BORDER"]};
        background: {c["BG"]};
        alternate-background-color: {c["BG_ALT"]};
        selection-background-color: {c["SELECTION"]};
        color: {c["FG"]};
    }}
    QHeaderView::section {{
        background: {c["BG_ALT"]};
        border: none;
        border-bottom: 1px solid {c["BORDER"]};
        padding: 4px 8px;
        font-weight: bold;
        font-size: {sm}pt;
        color: {c["FG_MUTED"]};
    }}
    QTreeWidget, QListWidget {{
        background: {c["BG"]};
        alternate-background-color: {c["BG_ALT"]};
        color: {c["FG"]};
        outline: none;
    }}
    QTreeWidget::item, QListWidget::item {{
        padding: 3px 4px;
    }}
    QTreeWidget::item:selected, QListWidget::item:selected {{
        background: {c["SELECTION"]};
        color: {c["FG"]};
    }}
    QTreeWidget::item:hover, QListWidget::item:hover {{
        background: {c["BG_ALT"]};
    }}

    /* -- Splitter -- */
    QSplitter::handle {{
        background: None;
    }}
    QSplitter::handle:horizontal {{
        width: 1px;
        margin: 0px 2px;
    }}
    QSplitter::handle:vertical {{
        height: 1px;
        margin: 2px 0px;
    }}
    QSplitter::handle:hover {{
        background: {c["BLUE"]};
    }}

    /* -- Scrollbars -- */
    QScrollBar:vertical {{
        width: 8px;
        background: transparent;
    }}
    QScrollBar::handle:vertical {{
        background: {c["BORDER"]};
        border-radius: 4px;
        min-height: 24px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: {c["FG_SUBTLE"]};
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0px;
    }}
    QScrollBar:horizontal {{
        height: 8px;
        background: transparent;
    }}
    QScrollBar::handle:horizontal {{
        background: {c["BORDER"]};
        border-radius: 4px;
        min-width: 24px;
    }}
    QScrollBar::handle:horizontal:hover {{
        background: {c["FG_SUBTLE"]};
    }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
        width: 0px;
    }}

    /* -- Status/menu -- */
    QStatusBar {{
        background: {c["BG_ALT"]};
        border-top: 1px solid {c["BORDER"]};
        font-size: {sm}pt;
        color: {c["FG_MUTED"]};
    }}
    QMenuBar {{
        background: {c["BG_ALT"]};
        border-bottom: 1px solid {c["BORDER"]};
        padding: 2px;
        color: {c["FG"]};
    }}
    QMenuBar::item {{
        padding: 4px 10px;
        border-radius: 3px;
        margin-top: 3px;
    }}
    QMenuBar::item:selected {{
        background: {c["BORDER"]};
    }}
    QMenuBar #menuBarWindowTitleContainer {{
        border-right: 1px solid {c["BORDER_FCS"]};
    }}
    QMenu {{
        background: {c["BG"]};
        color: {c["FG"]};
        border: 1px solid {c["BORDER"]};
        padding: 4px 0;
    }}
    QMenu::item {{
        padding: 5px 28px 5px 12px;
    }}
    QMenu::item:selected {{
        background: {c["SELECTION"]};
    }}
    QMenu::separator {{
        height: 1px;
        background: {c["BORDER"]};
        margin: 4px 8px;
    }}

    /* -- Group boxes and helper text -- */
    QGroupBox {{
        border: 1px solid {c["BORDER"]};
        border-radius: 4px;
        margin-top: 8px;
        padding-top: 16px;
        color: {c["FG"]};
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        left: 12px;
        padding: 0 4px;
    }}
    QCheckBox {{
        spacing: 6px;
        font-size: {sm}pt;
        color: {c["FG"]};
    }}
    QRadioButton {{
        spacing: 6px;
        font-size: {sm}pt;
        color: {c["FG"]};
    }}
    QToolTip {{
        background: {c["FG"]};
        color: {c["BG"]};
        border: none;
        padding: 4px 8px;
        font-size: {sm}pt;
    }}
    QFormLayout QLabel {{
        color: {c["FG_MUTED"]};
    }}
    QLabel#mutedLabel {{
        color: {c["FG_MUTED"]};
        font-size: {sm}pt;
    }}

    /* -- Intelligence panel -- */
    QWidget#intelligencePanel {{
        background: {c["BG"]};
        color: {c["FG"]};
    }}
    QLabel#intelSummary {{
        color: {c["FG"]};
    }}
    QLabel#intelCategory {{
        color: {c["FG_MUTED"]};
        font-size: {sm}pt;
        font-weight: bold;
    }}
    QFrame#intelCard {{
        border: 1px solid {c["BORDER"]};
        border-radius: 6px;
        background: {c["BG_ALT"]};
        color: {c["FG"]};
    }}
    QLabel#intelTitle, QLabel#intelDescription {{
        color: {c["FG"]};
        background: transparent;
    }}
    QLabel#intelRecommendation {{
        color: {c["FG_MUTED"]};
        background: transparent;
        font-size: {sm}pt;
    }}
    QLabel#intelSeverityIcon {{
        font-weight: bold;
        background: transparent;
    }}
    QLabel#intelSeverityIcon[severity="critical"] {{
        color: {c["RED"]};
    }}
    QLabel#intelSeverityIcon[severity="warning"] {{
        color: {c["AMBER"]};
    }}
    QLabel#intelSeverityIcon[severity="info"] {{
        color: {c["BLUE"]};
    }}
    QLabel#intelSeverityBadge {{
        border: 1px solid {c["BORDER"]};
        border-radius: 9px;
        padding: 0 6px;
        font-size: {sm}pt;
        color: {c["FG_MUTED"]};
        background: transparent;
    }}
    QLabel#intelSeverityBadge[severity="critical"] {{
        border-color: {c["RED"]};
        color: {c["RED"]};
    }}
    QLabel#intelSeverityBadge[severity="warning"] {{
        border-color: {c["AMBER"]};
        color: {c["AMBER"]};
    }}
    QLabel#intelSeverityBadge[severity="info"] {{
        border-color: {c["BLUE"]};
        color: {c["BLUE"]};
    }}
    QLabel#intelDetails {{
        border: 1px solid {c["BORDER"]};
        border-radius: 4px;
        padding: 6px;
        background: {c["BG"]};
        color: {c["FG"]};
    }}
    QPushButton#intelActionBtn {{
        min-height: 22px;
        padding: 2px 8px;
        border-radius: 4px;
        border: 1px solid {c["BORDER"]};
        background: {c["BG"]};
        color: {c["FG"]};
    }}
    QPushButton#intelActionBtn:hover {{
        background: {c["BG_ALT"]};
    }}
    QListWidget#intelAuditList {{
        border: 1px solid {c["BORDER"]};
        border-radius: 4px;
        background: {c["BG"]};
        color: {c["FG_MUTED"]};
    }}

    /* -- Sliders / spin boxes -- */
    QSlider::groove:horizontal {{
        height: 4px;
        background: {c["BORDER"]};
        border-radius: 2px;
    }}
    QSlider::handle:horizontal {{
        width: 14px;
        height: 14px;
        margin: -5px 0;
        background: {c["BLUE"]};
        border-radius: 7px;
    }}
    QSlider::handle:horizontal:hover {{
        background: {c["SEND_HOVER"]};
    }}
    QSpinBox {{
        border: 1px solid {c["BORDER"]};
        border-radius: 4px;
        padding: 2px 4px;
        background: {c["BG"]};
        color: {c["FG"]};
    }}
    QSpinBox:focus {{
        border-color: {c["BORDER_FCS"]};
    }}

    /* -- Dialog details -- */
    QDialogButtonBox QPushButton {{
        min-width: 80px;
    }}
    QMessageBox QLabel {{
        color: {c["FG"]};
    }}
    QMessageBox QTextEdit, QMessageBox QPlainTextEdit {{
        background: {c["BG"]};
        color: {c["FG"]};
        border: 1px solid {c["BORDER"]};
    }}
    """

