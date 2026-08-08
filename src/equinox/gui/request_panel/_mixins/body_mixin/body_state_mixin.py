"""Body-type switching and request-tab badge helpers for ``RequestPanel``."""

from __future__ import annotations

import logging
from typing import Any

from PyQt6.QtWidgets import QTableWidget

from equinox.application.requests import detect_body_type

logger = logging.getLogger(__name__)

_LABEL_CAPTURES = "Captures"


class BodyStateMixin:
    """Manage body-mode widget state and tab-label badges."""

    def _on_body_type_changed(self: Any, _index: int) -> None:
        """Show the correct body editor for the currently selected body type."""
        selection = self.body_type_combo.currentText()
        is_multipart = selection == "multipart/form-data"
        is_json = selection == "raw (JSON)"
        is_graphql = selection == "GraphQL"
        self.body_text.setVisible(not is_multipart and not is_graphql)
        self._multipart_table.setVisible(is_multipart)
        self._mp_toolbar.setVisible(is_multipart)
        self._fmt_json_btn.setVisible(is_json)
        self._gql_widget.setVisible(is_graphql)
        self._configure_body_placeholder(selection, is_multipart, is_graphql)
        self._update_tab_labels()

    def _configure_body_placeholder(
        self: Any,
        selection: str,
        is_multipart: bool,
        is_graphql: bool,
    ) -> None:
        """Update the raw-body editor enablement and placeholder text."""
        if selection == "none":
            self.body_text.setEnabled(False)
            self.body_text.setPlaceholderText("(no body)")
            return
        if is_multipart or is_graphql:
            return
        self.body_text.setEnabled(True)
        placeholders = {
            "raw (JSON)": '{\n  "key": "value"\n}',
            "raw (XML)": "<root>\n  <item>value</item>\n</root>",
            "raw (text)": "Plain text body",
            "form-urlencoded": "key1=value1&key2=value2",
        }
        self.body_text.setPlaceholderText(placeholders.get(selection, ""))

    def _set_tab_text_by_base_label(self: Any, base_label: str, text: str) -> None:
        """Update the first tab whose label matches *base_label* ignoring badges."""
        for index in range(self.tabs.count()):
            if self.tabs.tabText(index).startswith(base_label):
                self.tabs.setTabText(index, text)
                return

    @staticmethod
    def _non_empty_table_rows(table: QTableWidget, key_column: int = 0) -> int:
        """Count rows whose key column contains user-entered data."""
        count = 0
        for row in range(table.rowCount()):
            item = table.item(row, key_column)
            if item and item.text().strip():
                count += 1
        return count

    def _update_tab_labels(self: Any, *_args: Any) -> None:
        """Update request tab labels to show counts and presence markers."""
        try:
            self._update_header_and_param_labels()
            self._update_body_tab_label()
            self._update_capture_tab_label()
            self._update_presence_badges()
        except Exception:
            logger.exception("Failed to update tab labels", exc_info=True)

    def _update_header_and_param_labels(self: Any) -> None:
        """Refresh badge counts for the Headers and Params tabs."""
        header_count = len(self.headers_table.get_data())
        query_param_count = len(self.params_table.get_all_rows())
        path_param_count = self.path_params_table.rowCount()
        total_params = query_param_count + path_param_count
        self._set_tab_text_by_base_label(
            "Headers",
            f"Headers ({header_count})" if header_count else "Headers",
        )
        self._set_tab_text_by_base_label(
            "Params",
            f"Params ({total_params})" if total_params else "Params",
        )

    def _update_body_tab_label(self: Any) -> None:
        """Refresh the Body tab badge for the selected body mode."""
        body_type = self.body_type_combo.currentText()
        if body_type == "multipart/form-data":
            multipart_count = len(self._get_multipart_data())
            label = f"Body ({multipart_count})" if multipart_count else "Body"
            self._set_tab_text_by_base_label("Body", label)
            return
        has_text_body = body_type != "none" and bool(self.body_text.toPlainText().strip())
        self._set_tab_text_by_base_label("Body", "Body ÔùÅ" if has_text_body else "Body")

    def _update_capture_tab_label(self: Any) -> None:
        """Refresh the Captures tab badge based on non-empty capture rules."""
        capture_count = self._non_empty_table_rows(self.captures_table)
        label = f"{_LABEL_CAPTURES} ({capture_count})" if capture_count else _LABEL_CAPTURES
        self._set_tab_text_by_base_label(_LABEL_CAPTURES, label)

    def _update_presence_badges(self: Any) -> None:
        """Refresh simple presence badges for scripts and notes."""
        has_scripts = bool(
            self.pre_script_editor.toPlainText().strip()
            or self.post_script_editor.toPlainText().strip(),
        )
        has_notes = bool(self.notes_editor.toPlainText().strip())
        self._set_tab_text_by_base_label("Scripts", "Scripts ÔùÅ" if has_scripts else "Scripts")
        self._set_tab_text_by_base_label("Notes", "Notes ÔùÅ" if has_notes else "Notes")

    @staticmethod
    def _detect_body_type(body: str, headers: dict[str, str] | None = None) -> str:
        """Guess a body type using application-layer detection logic."""
        return str(detect_body_type(body, headers))
