"""Reusable show/hide toggle for password fields."""

from PyQt6.QtWidgets import QHBoxLayout, QLineEdit, QToolButton


def make_secret_row(line_edit: QLineEdit) -> QHBoxLayout:
    """Wrap a password QLineEdit with a show/hide eye-toggle button."""
    row = QHBoxLayout()
    row.setSpacing(2)
    row.addWidget(line_edit, 1)
    toggle = QToolButton()
    toggle.setCheckable(True)
    toggle.setText("\U0001f441")  # 👁
    toggle.setFixedWidth(28)
    toggle.setToolTip("Show / hide")
    toggle.setStyleSheet("QToolButton { border: none; font-size: 14px; }")
    toggle.toggled.connect(
        lambda checked: line_edit.setEchoMode(
            QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
        )
    )
    row.addWidget(toggle)
    return row
