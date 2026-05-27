"""Body search and highlight helpers for ``RequestPanel``."""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from PyQt6.QtGui import QColor
from PyQt6.QtGui import QTextCharFormat
from PyQt6.QtGui import QTextCursor
from PyQt6.QtGui import QTextDocument
from PyQt6.QtWidgets import QTextEdit

logger = logging.getLogger(__name__)

_MAX_HIGHLIGHTS = 500


class BodySearchMixin:
    """Search plain text, regex, and simple JSON paths within the body editor."""

    body_text: Any
    _body_case_cb: Any
    _body_regex_cb: Any
    _body_jsonpath_cb: Any

    @staticmethod
    def _select_range(target: QTextEdit, start: int, end: int) -> None:
        """Select a text range while clamping to the current document bounds."""
        try:
            document = target.document()
            if document is None:
                return
            max_pos = max(0, document.characterCount() - 1)
        except Exception:
            return
        safe_start = max(0, min(start, max_pos))
        safe_end = max(0, min(end, max_pos))
        if safe_start >= safe_end:
            return
        cursor = target.textCursor()
        cursor.setPosition(safe_start)
        cursor.setPosition(safe_end, QTextCursor.MoveMode.KeepAnchor)
        target.setTextCursor(cursor)

    def _body_editor_target(self) -> tuple[Any, str]:
        """Return the live body editor widget and its current text when available."""
        has_widget = getattr(self.body_text, "_has_widget", lambda: False)()
        target = getattr(self.body_text, "_widget", None) if has_widget else None
        text = target.toPlainText() if target is not None else getattr(self.body_text, "_buffer", "")
        return target, text

    @property
    def _re_flags(self) -> int:
        """Return regex flags based on the case-sensitive search toggle."""
        case_sensitive = bool(getattr(self, "_body_case_cb", None) and self._body_case_cb.isChecked())
        return 0 if case_sensitive else re.IGNORECASE

    @staticmethod
    def _make_extra_selection(target: Any, start: int, end: int, fmt: QTextCharFormat) -> QTextEdit.ExtraSelection | None:
        """Build an extra selection spanning ``[start, end)`` when valid."""
        try:
            document = target.document()
            if document is None:
                return None
            max_pos = max(0, document.characterCount() - 1)
        except Exception:
            return None
        safe_start = max(0, min(start, max_pos))
        safe_end = max(0, min(end, max_pos))
        if safe_start >= safe_end:
            return None
        selection = QTextEdit.ExtraSelection()
        cursor = target.textCursor()
        cursor.setPosition(safe_start)
        cursor.setPosition(safe_end, QTextCursor.MoveMode.KeepAnchor)
        selection.cursor = cursor
        selection.format = fmt
        return selection

    def _body_highlight_all(self) -> None:
        """Highlight all matches for the active body search term."""
        try:
            target, doc_text, term = self._current_body_search()
            if self._highlight_jsonpath_match(term, target):
                return
            selections = self._collect_search_selections(term, target, doc_text)
            if target is not None:
                target.setExtraSelections(selections[:_MAX_HIGHLIGHTS])
        except RuntimeError:
            logger.debug("Body editor unavailable while highlighting", exc_info=True)
        except Exception:
            logger.debug("Error during body highlight", exc_info=True)

    def _current_body_search(self) -> tuple[Any, str, str]:
        """Return the search target, document text, and current search term."""
        term_input = getattr(self, "_body_search_input", None)
        term = term_input.text() if term_input is not None else ""
        target, doc_text = self._body_editor_target()
        return target, doc_text, term

    def _highlight_jsonpath_match(self, term: str, target: Any) -> bool:
        """Highlight the first JSONPath result when JSONPath search mode is active."""
        jsonpath_mode = bool(getattr(self, "_body_jsonpath_cb", None) and self._body_jsonpath_cb.isChecked())
        if not jsonpath_mode or not term:
            return False
        positions = self._find_jsonpath_positions(term)
        if positions and target is not None:
            start, length = positions[0]
            self._select_range(target, start, start + length)
        return True

    def _collect_search_selections(self, term: str, target: Any, doc_text: str) -> list[QTextEdit.ExtraSelection]:
        """Collect highlight selections for plain-text or regex searches."""
        if target is None:
            return []
        formatter = QTextCharFormat()
        formatter.setBackground(QColor("#fff59d"))
        if getattr(self, "_body_regex_cb", None) and self._body_regex_cb.isChecked():
            return self._collect_regex_selections(term, target, doc_text, formatter)
        return self._collect_plain_text_selections(term, target, doc_text, formatter)

    def _collect_regex_selections(
        self,
        term: str,
        target: Any,
        doc_text: str,
        formatter: QTextCharFormat,
    ) -> list[QTextEdit.ExtraSelection]:
        """Collect highlight selections for a regex search."""
        if not term:
            target.setExtraSelections([])
            return []
        selections: list[QTextEdit.ExtraSelection] = []
        try:
            for match in re.finditer(term, doc_text, self._re_flags):
                selection = self._make_extra_selection(target, match.start(), match.end(), formatter)
                if selection is not None:
                    selections.append(selection)
                if len(selections) >= _MAX_HIGHLIGHTS:
                    break
        except re.error:
            return []
        return selections

    def _collect_plain_text_selections(
        self,
        term: str,
        target: Any,
        doc_text: str,
        formatter: QTextCharFormat,
    ) -> list[QTextEdit.ExtraSelection]:
        """Collect highlight selections for a plain-text search."""
        if not term:
            return []
        case_sensitive = bool(getattr(self, "_body_case_cb", None) and self._body_case_cb.isChecked())
        haystack = doc_text if case_sensitive else doc_text.lower()
        needle = term if case_sensitive else term.lower()
        selections: list[QTextEdit.ExtraSelection] = []
        start = 0
        index = haystack.find(needle, start)
        while index != -1:
            selection = self._make_extra_selection(target, index, index + len(needle), formatter)
            if selection is not None:
                selections.append(selection)
            if len(selections) >= _MAX_HIGHLIGHTS:
                break
            start = index + max(1, len(needle))
            index = haystack.find(needle, start)
        return selections

    def _body_navigate(self, *, forward: bool) -> None:
        """Move to the next or previous search match in the body editor."""
        try:
            term = self._get_search_term()
            if not term:
                return
            target, doc_text = self._body_editor_target()
            if target is None:
                return
            if self._is_jsonpath_mode():
                self._navigate_jsonpath(term, target, forward)
                return
            if self._is_regex_mode():
                self._navigate_regex(term, target, doc_text, forward)
                return
            self._navigate_plain_text(term, target, forward)
        except RuntimeError:
            direction = "next" if forward else "prev"
            logger.debug("Body editor unavailable during find %s", direction, exc_info=True)
        except Exception:
            logger.debug("Error during body navigate", exc_info=True)

    def _get_search_term(self) -> str:
        """Return the current body search term."""
        term_input = getattr(self, "_body_search_input", None)
        if term_input is None:
            return ""
        try:
            return term_input.text() or ""
        except Exception:
            return ""

    def _is_jsonpath_mode(self) -> bool:
        """Return ``True`` when JSONPath search mode is enabled."""
        checkbox = getattr(self, "_body_jsonpath_cb", None)
        return bool(checkbox and checkbox.isChecked())

    def _is_regex_mode(self) -> bool:
        """Return ``True`` when regex search mode is enabled."""
        checkbox = getattr(self, "_body_regex_cb", None)
        return bool(checkbox and checkbox.isChecked())

    def _navigate_jsonpath(self, term: str, target: Any, forward: bool) -> None:
        """Navigate to the first or last JSONPath match."""
        positions = self._find_jsonpath_positions(term)
        if not positions:
            return
        start, length = positions[0] if forward else positions[-1]
        self._select_range(target, start, start + length)

    def _navigate_regex(self, term: str, target: Any, doc_text: str, forward: bool) -> None:
        """Navigate through regex matches with wrap-around semantics."""
        current_position = target.textCursor().position()
        start, end = self._regex_next(term, doc_text, current_position)
        if not forward:
            start, end = self._regex_prev(term, doc_text, current_position)
        if start is not None and end is not None:
            self._select_range(target, start, end)

    def _regex_next(self, term: str, doc_text: str, current_position: int) -> tuple[int | None, int | None]:
        """Return the next regex match relative to the cursor position."""
        match = re.search(term, doc_text[current_position:], self._re_flags)
        if match:
            return current_position + match.start(), current_position + match.end()
        match = re.search(term, doc_text, self._re_flags)
        if match:
            return match.start(), match.end()
        return None, None

    def _regex_prev(self, term: str, doc_text: str, current_position: int) -> tuple[int | None, int | None]:
        """Return the previous regex match relative to the cursor position."""
        matches = [match for match in re.finditer(term, doc_text, self._re_flags) if match.start() < current_position]
        if not matches:
            matches = list(re.finditer(term, doc_text, self._re_flags))
        if not matches:
            return None, None
        match = matches[-1]
        return match.start(), match.end()

    def _navigate_plain_text(self, term: str, target: Any, forward: bool) -> None:
        """Navigate through plain-text matches with wrap-around semantics."""
        if forward:
            if not target.find(term):
                self._wrap_to_start(target)
                target.find(term)
            return
        if not target.find(term, QTextDocument.FindFlag.FindBackward):
            self._wrap_to_end(target)
            target.find(term, QTextDocument.FindFlag.FindBackward)

    def _wrap_to_start(self, target: Any) -> None:
        """Move the target cursor to the start of the document."""
        cursor = target.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        target.setTextCursor(cursor)

    def _wrap_to_end(self, target: Any) -> None:
        """Move the target cursor to the end of the document."""
        cursor = target.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        target.setTextCursor(cursor)

    def _body_find_next(self) -> None:
        """Jump to the next body search match."""
        self._body_navigate(forward=True)

    def _body_find_prev(self) -> None:
        """Jump to the previous body search match."""
        self._body_navigate(forward=False)

    def _find_jsonpath_positions(self, path: str) -> list[tuple[int, int]]:
        """Locate the value selected by a simple dotted or bracketed JSON path."""
        _, text = self._body_editor_target()
        document = self._parse_body_json(text)
        steps = self._parse_simple_jsonpath(path)
        if document is None or steps is None:
            return []
        matched = self._resolve_simple_jsonpath(document, steps)
        if matched is None:
            return []
        return self._locate_unique_json_fragment(text, matched, path)

    @staticmethod
    def _parse_body_json(text: str) -> Any | None:
        """Parse the current body text as JSON."""
        if not text:
            return None
        try:
            return json.loads(text)
        except Exception:
            return None

    @staticmethod
    def _parse_simple_jsonpath(path: str) -> list[Any] | None:
        """Parse a simple ``a.b[0]['c']``-style JSON path into steps."""
        steps: list[Any] = []
        index = 0
        while index < len(path):
            char = path[index]
            if char == ".":
                index += 1
                continue
            if char == "[":
                end = path.find("]", index)
                if end == -1:
                    return None
                token = path[index + 1 : end]
                steps.append(int(token) if token.isdigit() else token.strip("\"'"))
                index = end + 1
                continue
            end = index
            while end < len(path) and path[end] not in ".[":
                end += 1
            steps.append(path[index:end])
            index = end
        return steps

    @staticmethod
    def _resolve_simple_jsonpath(document: Any, steps: list[Any]) -> Any | None:
        """Walk a parsed JSON path against a parsed JSON document."""
        current = document
        for step in steps:
            if isinstance(step, int):
                if not isinstance(current, list) or not 0 <= step < len(current):
                    return None
                current = current[step]
                continue
            if not isinstance(current, dict) or step not in current:
                return None
            current = current[step]
        return current

    def _locate_unique_json_fragment(self, text: str, value: Any, path: str) -> list[tuple[int, int]]:
        """Locate a uniquely matching JSON fragment within the source body text."""
        try:
            fragment = json.dumps(value, ensure_ascii=False)
        except Exception:
            return []
        start = text.find(fragment)
        if start < 0:
            return []
        duplicate = text.find(fragment, start + 1)
        if duplicate >= 0:
            logger.debug("JSONPath highlight skipped due to ambiguous value match path=%s", path)
            return []
        return [(start, len(fragment))]
