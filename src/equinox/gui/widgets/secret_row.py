"""Reusable show/hide toggle for password fields."""

from PyQt6.QtWidgets import QHBoxLayout, QLineEdit, QToolButton


def make_secret_row(line_edit: QLineEdit) -> QHBoxLayout:
    """Wrap a password QLineEdit with a show/hide eye-toggle button.

    The toggle's initial checked state is derived from *line_edit*'s current
    echo mode so callers that pre-configure the echo mode are handled
    correctly instead of always starting in the hidden (Password) position.
    """
    row = QHBoxLayout()
    row.setSpacing(2)
    row.addWidget(line_edit, 1)

    toggle = QToolButton()
    toggle.setCheckable(True)
    toggle.setText("\U0001f441")  # 👁
    toggle.setFixedWidth(28)
    toggle.setToolTip("Show / hide")
    toggle.setStyleSheet("QToolButton { border: none; font-size: 14px; }")

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
            # teardown order).  The toggle has nothing to update; swallow
            # silently rather than crashing the event loop.
            pass

    toggle.toggled.connect(_on_toggle)
    row.addWidget(toggle)
    return row
