"""Reusable show/hide toggle for password fields."""

import logging

from PyQt6.QtWidgets import QHBoxLayout, QLineEdit, QToolButton

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Visual configuration — change here, takes effect everywhere
# ---------------------------------------------------------------------------

_EYE_ICON: str = "\U0001f441"   # 👁  Unicode eye glyph
_ROW_SPACING: int = 2            # Gap between the line-edit and the toggle (px)
_TOGGLE_WIDTH: int = 28          # Fixed width of the eye-button (px)
_TOGGLE_STYLE: str = "QToolButton { border: none; font-size: 14px; }"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def make_secret_row(line_edit: QLineEdit) -> QHBoxLayout:
    """Wrap a password QLineEdit with a show/hide eye-toggle button.

    The toggle's initial checked state is derived from *line_edit*'s current
    echo mode so callers that pre-configure the echo mode are handled
    correctly instead of always starting in the hidden (Password) position.
    """
    row = QHBoxLayout()
    row.setSpacing(_ROW_SPACING)
    row.addWidget(line_edit, 1)

    toggle = QToolButton()
    toggle.setCheckable(True)
    toggle.setText(_EYE_ICON)
    toggle.setFixedWidth(_TOGGLE_WIDTH)
    toggle.setToolTip("Show / hide")
    toggle.setStyleSheet(_TOGGLE_STYLE)

    # Sync the initial checked state with the line edit's *current* echo mode.
    # Without this, a line_edit already in Normal mode starts with a mismatched
    # toggle (unchecked = Password), confusing the user on first interaction.
    toggle.setChecked(line_edit.echoMode() == QLineEdit.EchoMode.Normal)

    def _on_toggle(checked: bool) -> None:
        try:
            line_edit.setEchoMode(
                QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
            )
        except RuntimeError:
            # line_edit's underlying C++ object was already destroyed (unusual
            # teardown order).  Log at DEBUG so genuine bugs remain visible
            # while normal shutdown races are not treated as errors.
            logger.debug(
                "make_secret_row: QLineEdit already destroyed; toggle ignored"
            )

    toggle.toggled.connect(_on_toggle)
    row.addWidget(toggle)
    return row
