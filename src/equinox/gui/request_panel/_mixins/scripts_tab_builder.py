"""Scripts-tab UI builder helpers for RequestPanel."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QGroupBox,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from equinox.gui.syntax_highlighter.python_highlighter import PythonHighlighter
from equinox.gui.theme import get_mono_font


def build_script_section(
    title: str,
    placeholder: str,
) -> tuple[QGroupBox, QPlainTextEdit, QLabel]:
    """Build a labelled script-editor group box."""
    group = QGroupBox(title)
    group_layout = QVBoxLayout(group)
    group_layout.setContentsMargins(4, 6, 4, 4)

    editor = QPlainTextEdit()
    editor.setPlaceholderText(placeholder)
    editor.setFont(get_mono_font())

    result_label = QLabel("")
    result_label.setWordWrap(True)

    group_layout.addWidget(editor)
    group_layout.addWidget(result_label)
    return group, editor, result_label


def create_scripts_tab(panel: object, cheat_text: str) -> QWidget:
    """Build scripts tab with pre/post editors, syntax highlighting and cheatsheet."""
    widget = QWidget()
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(4, 6, 4, 4)

    splitter = QSplitter(Qt.Orientation.Vertical)

    pre_group, panel.pre_script_editor, panel.pre_script_result = build_script_section(
        "Pre-request Script",
        "# Runs before the request is sent\n"
        "# Available: request (dict), env (dict)\n"
        "# Example: env['timestamp'] = str(int(__import__('time').time()))",
    )
    splitter.addWidget(pre_group)

    post_group, panel.post_script_editor, panel.post_script_result = build_script_section(
        "Post-response Script",
        "# Runs after response is received\n"
        "# Available: response (dict with status_code, headers, body, json), env (dict)\n"
        "# Example: env['user_id'] = str(response['json']['id'])",
    )
    splitter.addWidget(post_group)

    splitter.setSizes([300, 300])
    splitter.setChildrenCollapsible(False)
    splitter.setHandleWidth(5)
    layout.addWidget(splitter, 1)

    panel._pre_highlighter = PythonHighlighter(panel.pre_script_editor.document())
    panel._post_highlighter = PythonHighlighter(panel.post_script_editor.document())

    cheat_toggle = QPushButton()
    cheat_toggle.setText("▶ Available variables & modules")
    cheat_toggle.setCheckable(True)
    cheat_toggle.setFlat(True)
    layout.addWidget(cheat_toggle)

    cheat_label = QLabel(cheat_text)
    cheat_label.setTextFormat(Qt.TextFormat.RichText)
    cheat_label.setObjectName("mutedLabel")
    cheat_label.setVisible(False)
    cheat_label.setWordWrap(True)
    cheat_label.setContentsMargins(8, 2, 8, 4)
    layout.addWidget(cheat_label)
    cheat_toggle.toggled.connect(
        lambda checked: (
            cheat_label.setVisible(checked),
            cheat_toggle.setText(
                "▼ Available variables & modules" if checked else "▶ Available variables & modules"
            ),
        )
    )

    return widget
