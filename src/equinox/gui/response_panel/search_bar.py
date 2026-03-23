"""Inline find-bar for a QTextEdit — shown/hidden with Ctrl+F."""

import json
from typing import Optional

from PyQt6.QtGui import QTextCharFormat, QColor, QTextCursor, QTextDocument
from PyQt6.QtWidgets import (
    QWidget, QTextEdit, QHBoxLayout, QVBoxLayout,
    QLineEdit, QLabel, QToolButton,
)

from equinox.gui.theme import Colors


class SearchBar(QWidget):
    """Inline find-bar for a QTextEdit — shown/hidden with Ctrl+F.

    Three mutually-exclusive search modes are available via toggle buttons on
    the right side of the bar:

    * **Plain text** (default) — literal substring match.  The ``Aa`` button
      enables case-sensitive searching; without it the search is
      case-insensitive.
    * **Regex** (``.*``) — Qt ``FindRegEx`` flag; the same ``Aa`` button
      controls case-sensitivity.  Mutually exclusive with JSONPath mode.
    * **JSONPath** (``$.``) — the expression is evaluated against the JSON
      document supplied via :meth:`set_json_doc`.  Primitive matched values
      (strings, numbers, booleans, null) are highlighted in the text editor;
      a compact result strip below the bar shows the resolved values.
      Mutually exclusive with Regex mode.

    Requires ``jsonpath-ng`` for JSONPath mode (already in project
    requirements); gracefully shows an error message when not installed.
    """

    def __init__(self, target: QTextEdit, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._target = target
        self._json_obj = None           # set via set_json_doc()
        self._matches: list = []        # list[QTextCursor] — from _collect_matches
        self._current_idx: int = -1
        self.setVisible(False)

        # ── Outer layout: main row + optional JSONPath result strip ───
        outer = QVBoxLayout(self)
        outer.setContentsMargins(2, 2, 2, 2)
        outer.setSpacing(2)

        # ── Main row ──────────────────────────────────────────────────
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)

        self._input = QLineEdit()
        self._input.setPlaceholderText("Find in body…")
        self._input.setFixedHeight(24)
        self._input.returnPressed.connect(self._find_next)
        self._input.textChanged.connect(self._on_text_changed)

        self._match_label = QLabel("")
        self._match_label.setObjectName("mutedLabel")
        self._match_label.setMinimumWidth(90)

        prev_btn = QToolButton()
        prev_btn.setText("▲")
        prev_btn.setFixedSize(24, 24)
        prev_btn.setToolTip("Find previous (Shift+Enter)")
        next_btn = QToolButton()
        next_btn.setText("▼")
        next_btn.setFixedSize(24, 24)
        next_btn.setToolTip("Find next (Enter)")

        # Mode toggles — Aa and .* can combine; .* and $. are mutually exclusive
        self._case_btn = QToolButton()
        self._case_btn.setText("Aa")
        self._case_btn.setFixedSize(28, 24)
        self._case_btn.setCheckable(True)
        self._case_btn.setChecked(False)
        self._case_btn.setToolTip("Match case")

        self._regex_btn = QToolButton()
        self._regex_btn.setText(".*")
        self._regex_btn.setFixedSize(28, 24)
        self._regex_btn.setCheckable(True)
        self._regex_btn.setChecked(False)
        self._regex_btn.setToolTip("Use regular expression")

        self._jp_btn = QToolButton()
        self._jp_btn.setText("$.")
        self._jp_btn.setFixedSize(28, 24)
        self._jp_btn.setCheckable(True)
        self._jp_btn.setChecked(False)
        self._jp_btn.setToolTip(
            "JSONPath filter — evaluate a JSONPath expression against the JSON body\n"
            "Example: $.users[*].name"
        )

        close_btn = QToolButton()
        close_btn.setText("✕")
        close_btn.setFixedSize(24, 24)
        close_btn.setStyleSheet(f"color: {Colors.FG_MUTED};")
        close_btn.setToolTip("Close search bar (Esc)")

        prev_btn.clicked.connect(self._find_prev)
        next_btn.clicked.connect(self._find_next)
        close_btn.clicked.connect(self.hide)
        self._case_btn.toggled.connect(self._on_mode_changed)
        self._regex_btn.toggled.connect(self._on_regex_toggled)
        self._jp_btn.toggled.connect(self._on_jsonpath_toggled)

        row.addWidget(QLabel("Find:"))
        row.addWidget(self._input, 1)
        row.addWidget(self._match_label)
        row.addWidget(prev_btn)
        row.addWidget(next_btn)
        row.addWidget(self._case_btn)
        row.addWidget(self._regex_btn)
        row.addWidget(self._jp_btn)
        row.addWidget(close_btn)
        outer.addLayout(row)

        # ── JSONPath result strip (shown only in JSONPath mode) ───────
        self._jp_result_label = QLabel("")
        self._jp_result_label.setObjectName("mutedLabel")
        self._jp_result_label.setWordWrap(True)
        self._jp_result_label.setContentsMargins(6, 0, 4, 2)
        self._jp_result_label.setVisible(False)
        outer.addWidget(self._jp_result_label)

    # ── Public API ────────────────────────────────────────────────────

    def set_json_doc(self, obj) -> None:
        """Provide the parsed JSON object for JSONPath evaluation.

        Call this after every response that contains JSON, passing the result
        of ``response.json()``.  Pass ``None`` to clear (non-JSON response).
        If the bar is currently in JSONPath mode the results update immediately.
        """
        self._json_obj = obj
        if self._jp_btn.isChecked() and self.isVisible():
            self._on_text_changed(self._input.text())

    def show_and_focus(self) -> None:
        self.setVisible(True)
        self._input.selectAll()
        self._input.setFocus()

    def hide(self) -> None:  # type: ignore[override]
        self.setVisible(False)
        self._matches = []
        self._current_idx = -1
        self._target.setExtraSelections([])
        self._jp_result_label.setVisible(False)
        self._target.setFocus()

    # ── Mode toggle handlers ──────────────────────────────────────────

    def _on_mode_changed(self) -> None:
        """Re-run the search when Aa (case) toggle changes."""
        self._on_text_changed(self._input.text())

    def _on_regex_toggled(self, checked: bool) -> None:
        """Ensure Regex and JSONPath modes are mutually exclusive."""
        if checked:
            # Turn off JSONPath if it was on
            self._jp_btn.blockSignals(True)
            self._jp_btn.setChecked(False)
            self._jp_btn.blockSignals(False)
            self._jp_result_label.setVisible(False)
            self._input.setPlaceholderText("Regular expression…")
        else:
            if not self._jp_btn.isChecked():
                self._input.setPlaceholderText("Find in body…")
        self._on_text_changed(self._input.text())

    def _on_jsonpath_toggled(self, checked: bool) -> None:
        """Ensure JSONPath and Regex modes are mutually exclusive."""
        if checked:
            # Turn off Regex if it was on
            self._regex_btn.blockSignals(True)
            self._regex_btn.setChecked(False)
            self._regex_btn.blockSignals(False)
            self._input.setPlaceholderText("JSONPath expression — e.g. $.users[*].name")
        else:
            self._jp_result_label.setVisible(False)
            if not self._regex_btn.isChecked():
                self._input.setPlaceholderText("Find in body…")
        self._on_text_changed(self._input.text())

    # ── Core search logic ─────────────────────────────────────────────

    def _on_text_changed(self, _text: str) -> None:
        self._collect_matches()
        self._current_idx = 0 if self._matches else -1
        self._apply_highlights()

    def _collect_matches(self) -> None:
        """Dispatch to the appropriate match collector for the active mode."""
        term = self._input.text()
        if not term:
            self._matches = []
            self._match_label.setText("")
            self._jp_result_label.setVisible(False)
            self._target.setExtraSelections([])
            return

        if self._jp_btn.isChecked():
            self._collect_matches_jsonpath(term)
        elif self._regex_btn.isChecked():
            self._collect_matches_regex(term)
        else:
            self._collect_matches_text(term)

    def _collect_matches_text(self, term: str) -> None:
        """Literal substring search, optionally case-sensitive."""
        flags = QTextDocument.FindFlag(0)
        if self._case_btn.isChecked():
            flags |= QTextDocument.FindFlag.FindCaseSensitively

        doc = self._target.document()
        cursor = QTextCursor(doc)
        matches = []
        while True:
            cursor = doc.find(term, cursor, flags)
            if cursor.isNull():
                break
            matches.append(QTextCursor(cursor))

        self._matches = matches
        count = len(matches)
        self._match_label.setText(
            f"{count} match{'es' if count != 1 else ''}" if count else "no matches"
        )

    def _collect_matches_regex(self, pattern: str) -> None:
        """Regular-expression search via Qt FindRegEx."""
        flags = QTextDocument.FindFlag.FindRegEx
        if self._case_btn.isChecked():
            flags |= QTextDocument.FindFlag.FindCaseSensitively

        doc = self._target.document()
        cursor = QTextCursor(doc)
        matches = []
        try:
            while True:
                cursor = doc.find(pattern, cursor, flags)
                if cursor.isNull():
                    break
                matches.append(QTextCursor(cursor))
        except Exception:
            self._matches = []
            self._match_label.setText("invalid regex")
            return

        self._matches = matches
        count = len(matches)
        self._match_label.setText(
            f"{count} match{'es' if count != 1 else ''}" if count else "no matches"
        )

    def _collect_matches_jsonpath(self, expr: str) -> None:
        """Evaluate a JSONPath expression and highlight matched primitive values.

        Steps:
        1. Parse and evaluate *expr* against ``_json_obj`` using jsonpath-ng.
        2. Show a compact preview of the resolved values in the result strip.
        3. For each *primitive* matched value, search the text editor for its
           JSON serialization (``"John"`` / ``42`` / ``true`` / ``null``) and
           collect those cursors as normal text-search matches so the ▲/▼
           navigation buttons work.  Complex (dict/list) values are skipped
           for text highlighting because they span multiple lines in a
           pretty-printed body.
        """
        if self._json_obj is None:
            self._matches = []
            self._match_label.setText("no JSON")
            self._jp_result_label.setText(
                "No JSON document available — send a request that returns JSON first."
            )
            self._jp_result_label.setVisible(True)
            return

        # ── Parse and evaluate ────────────────────────────────────────
        try:
            from jsonpath_ng.ext import parse as _jp_parse  # noqa: PLC0415
            jp_expr = _jp_parse(expr)
            raw_matches = jp_expr.find(self._json_obj)
        except ImportError:
            self._matches = []
            self._match_label.setText("jsonpath-ng missing")
            self._jp_result_label.setText(
                "⚠ jsonpath-ng is not installed.  Run:  pip install jsonpath-ng"
            )
            self._jp_result_label.setVisible(True)
            return
        except Exception as exc:
            self._matches = []
            self._match_label.setText("expression error")
            self._jp_result_label.setText(f"⚠ {exc}")
            self._jp_result_label.setVisible(True)
            return

        values = [m.value for m in raw_matches]
        count = len(values)
        self._match_label.setText(
            f"{count} match{'es' if count != 1 else ''}" if count else "no matches"
        )

        # ── Result strip ──────────────────────────────────────────────
        if values:
            previews = []
            for v in values[:6]:
                s = json.dumps(v, ensure_ascii=False)
                previews.append(s if len(s) <= 50 else s[:47] + "…")
            preview = "  ·  ".join(previews)
            if count > 6:
                preview += f"  … (+{count - 6} more)"
            self._jp_result_label.setText(f"→ {preview}")
        else:
            self._jp_result_label.setText("(path matched no values)")
        self._jp_result_label.setVisible(True)

        # ── Text-editor highlights for primitive values ───────────────
        doc = self._target.document()
        text_matches = []
        seen_terms: set = set()          # deduplicate identical serializations
        for v in values:
            if isinstance(v, (dict, list)):
                continue                 # skip — multi-line in pretty-print
            term = json.dumps(v, ensure_ascii=False)
            if term in seen_terms:
                continue
            seen_terms.add(term)
            cursor = QTextCursor(doc)
            while True:
                cursor = doc.find(
                    term, cursor, QTextDocument.FindFlag.FindCaseSensitively
                )
                if cursor.isNull():
                    break
                text_matches.append(QTextCursor(cursor))

        self._matches = text_matches
        self._current_idx = 0 if text_matches else -1

    # ── Highlight rendering ───────────────────────────────────────────

    def _apply_highlights(self) -> None:
        """Repaint extra selections: current match in orange, others in yellow."""
        if not self._matches:
            self._target.setExtraSelections([])
            return

        dim_fmt = QTextCharFormat()
        dim_fmt.setBackground(QColor(Colors.HIGHLIGHT))

        cur_fmt = QTextCharFormat()
        cur_fmt.setBackground(QColor("#e8a030"))  # orange — visible on both themes

        selections = []
        for i, cur in enumerate(self._matches):
            sel = QTextEdit.ExtraSelection()
            sel.cursor = cur
            sel.format = cur_fmt if i == self._current_idx else dim_fmt
            selections.append(sel)

        self._target.setExtraSelections(selections)

        if 0 <= self._current_idx < len(self._matches):
            self._target.setTextCursor(self._matches[self._current_idx])
            self._target.ensureCursorVisible()

    def _update_label(self) -> None:
        count = len(self._matches)
        if count == 0:
            self._match_label.setText("no matches")
        else:
            self._match_label.setText(
                f"{self._current_idx + 1} / {count} match{'es' if count != 1 else ''}"
            )

    # ── Navigation ────────────────────────────────────────────────────

    def _find_next(self) -> None:
        if not self._matches:
            return
        self._current_idx = (self._current_idx + 1) % len(self._matches)
        self._apply_highlights()
        self._update_label()

    def _find_prev(self) -> None:
        if not self._matches:
            return
        self._current_idx = (self._current_idx - 1) % len(self._matches)
        self._apply_highlights()
        self._update_label()
