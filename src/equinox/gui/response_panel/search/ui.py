"""
Qt UI layer for the search bar.
Depends on search.core but core does NOT depend on Qt.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtCore import QObject
from PyQt6.QtCore import QRunnable
from PyQt6.QtCore import QThreadPool
from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QColor
from PyQt6.QtGui import QTextCharFormat
from PyQt6.QtGui import QTextCursor
from PyQt6.QtWidgets import QHBoxLayout
from PyQt6.QtWidgets import QLabel
from PyQt6.QtWidgets import QLineEdit
from PyQt6.QtWidgets import QTextEdit
from PyQt6.QtWidgets import QToolButton
from PyQt6.QtWidgets import QVBoxLayout
from PyQt6.QtWidgets import QWidget

from .constants import ASYNC_MIN_DOC_CHARS
from .constants import DEBOUNCE_INTERVAL_MS
from .constants import ERROR_NO_JSON
from .constants import PLACEHOLDER_TEXT_FIND
from .constants import PLACEHOLDER_TEXT_JSONPATH
from .constants import PLACEHOLDER_TEXT_REGEX
from .constants import SEARCH_HIGHLIGHT_RADIUS
from .constants import STATUS_CANCELLED
from .core import SearchEngine
from .core import SearchJobConfig
from .core import SearchMode

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────
# Qt Runnable Wrapper
# ─────────────────────────────────────────────────────────────────────


class _SearchSignals(QObject):
    result = pyqtSignal(int, object)


class _SearchRunnable(QRunnable):
    def __init__(self, cfg: SearchJobConfig, engine: SearchEngine) -> None:
        super().__init__()
        self.cfg = cfg
        self.engine = engine
        self.signals = _SearchSignals()

    def run(self) -> None:
        result = self.engine.run(self.cfg)
        self.signals.result.emit(self.cfg.job_id, result)


# ─────────────────────────────────────────────────────────────────────
# SearchBar UI
# ─────────────────────────────────────────────────────────────────────


class SearchBar(QWidget):
    """Qt search bar widget wrapping the pure search engine."""

    def __init__(self, target: QTextEdit, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._target = target
        self._json_obj: Any = None
        self._offsets: list[tuple[int, int]] = []
        self._current_idx = -1
        self._matches: list[str] = []

        self._engine = SearchEngine()
        self._thread_pool = QThreadPool.globalInstance()

        self._job_counter = 0
        self._current_job_id = 0

        self._filter_cb: Callable[[str | None], None] | None = None

        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(DEBOUNCE_INTERVAL_MS)
        self._debounce_timer.timeout.connect(self._on_debounced)

        self._pending_text = ""

        self._build_ui()
        self.setVisible(False)

    # ────────────────────────────────────────────────────────────────
    # Public API
    # ────────────────────────────────────────────────────────────────

    def set_json_doc(self, obj: Any) -> None:
        self._json_obj = obj
        if obj is None and self._filter_cb:
            self._filter_cb(None)

    def set_filter_callback(self, cb: Callable[[str | None], None] | None) -> None:
        self._filter_cb = cb

    def show_and_focus(self) -> None:
        self.setVisible(True)
        self._start_search(self._input.text())
        self._input.selectAll()
        self._input.setFocus()

    def hide(self) -> None:
        super().hide()
        self._clear_state()
        self._target.setFocus()

    def reset(self) -> None:
        """Clear query, results, and highlights for a newly-displayed document.

        set_json_doc() alone leaves the previous query text, match count,
        and highlights in place - they describe the old body, not the one
        just loaded. Call this whenever the target document changes,
        whether or not the search bar is currently visible.
        """
        self._debounce_timer.stop()
        self._job_counter += 1
        self._current_job_id = self._job_counter
        self._pending_text = ""
        self._input.blockSignals(True)
        self._input.clear()
        self._input.blockSignals(False)
        self._match_label.setText("")
        self._clear_state()

    # ────────────────────────────────────────────────────────────────
    # UI Construction
    # ────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)

        row = QHBoxLayout()
        row.setSpacing(4)

        self._input = QLineEdit()
        self._input.setPlaceholderText(PLACEHOLDER_TEXT_FIND)
        self._input.textChanged.connect(self._on_text_changed)

        self._match_label = QLabel("")
        self._jp_label = QLabel("")
        self._jp_label.setWordWrap(True)
        self._jp_label.setVisible(False)

        self._case_btn = self._make_toggle("Aa", self._on_mode_changed)
        self._regex_btn = self._make_toggle(".*", self._on_regex_toggled)
        self._jp_btn = self._make_toggle("$. ", self._on_jsonpath_toggled)

        prev_btn = self._make_button("▲", self._find_prev)
        next_btn = self._make_button("▼", self._find_next)
        close_btn = self._make_button("✕", self.hide)

        row.addWidget(QLabel("Find:"))
        row.addWidget(self._input, 1)
        row.addWidget(self._match_label)
        row.addWidget(prev_btn)
        row.addWidget(next_btn)
        row.addWidget(self._case_btn)
        row.addWidget(self._regex_btn)
        row.addWidget(self._jp_btn)
        row.addWidget(close_btn)

        layout.addLayout(row)
        layout.addWidget(self._jp_label)

    def _make_button(self, text: str, slot: Callable) -> QToolButton:  # type: ignore[type-arg]
        btn = QToolButton()
        btn.setText(text)
        btn.clicked.connect(slot)
        return btn

    def _make_toggle(self, text: str, slot: Callable) -> QToolButton:  # type: ignore[type-arg]
        btn = QToolButton()
        btn.setText(text)
        btn.setCheckable(True)
        btn.toggled.connect(slot)
        return btn

    # ────────────────────────────────────────────────────────────────
    # Mode Handling
    # ────────────────────────────────────────────────────────────────

    def _current_mode(self) -> SearchMode:
        if self._jp_btn.isChecked():
            return SearchMode.JSONPATH
        if self._regex_btn.isChecked():
            return SearchMode.REGEX
        return SearchMode.TEXT

    def _on_mode_changed(self) -> None:
        self._start_search(self._input.text())

    def _on_regex_toggled(self, checked: bool) -> None:
        if checked:
            self._jp_btn.setChecked(False)
            self._input.setPlaceholderText(PLACEHOLDER_TEXT_REGEX)
        else:
            self._input.setPlaceholderText(PLACEHOLDER_TEXT_FIND)
        self._start_search(self._input.text())

    def _on_jsonpath_toggled(self, checked: bool) -> None:
        if checked:
            self._regex_btn.setChecked(False)
            self._input.setPlaceholderText(PLACEHOLDER_TEXT_JSONPATH)
        else:
            self._jp_label.setVisible(False)
            self._input.setPlaceholderText(PLACEHOLDER_TEXT_FIND)
            if self._filter_cb:
                self._filter_cb(None)
        self._start_search(self._input.text())

    # ────────────────────────────────────────────────────────────────
    # Debounced Input
    # ────────────────────────────────────────────────────────────────

    def _on_text_changed(self, text: str) -> None:
        self._pending_text = text
        self._match_label.setText("searching…")
        self._debounce_timer.start()

    def _on_debounced(self) -> None:
        self._start_search(self._pending_text)

    # ────────────────────────────────────────────────────────────────
    # Search Execution
    # ────────────────────────────────────────────────────────────────

    def _start_search(self, text: str) -> None:
        mode = self._current_mode()

        if mode is SearchMode.JSONPATH and self._json_obj is None:
            self._match_label.setText("no JSON")
            self._jp_label.setText(ERROR_NO_JSON)
            self._jp_label.setVisible(True)
            return

        self._job_counter += 1
        job_id = self._job_counter
        self._current_job_id = job_id

        cfg = SearchJobConfig(
            job_id=job_id,
            mode=mode,
            term=text,
            case_sensitive=self._case_btn.isChecked(),
            doc_text=self._target.toPlainText(),
            json_obj=self._json_obj,
        )

        runnable = _SearchRunnable(cfg, self._engine)
        runnable.signals.result.connect(self._on_result)

        if len(cfg.doc_text) >= ASYNC_MIN_DOC_CHARS:
            self._thread_pool.start(runnable)  # type: ignore[union-attr]
        else:
            runnable.run()

    def _start_search_job(self, text: str) -> None:
        """Backward-compatible search entrypoint used by legacy callers/tests."""
        self._start_search(text)

    def _on_cancel_search(self) -> None:
        """Cancel pending search work and reset the visible search state."""
        self._debounce_timer.stop()
        self._job_counter += 1
        self._current_job_id = self._job_counter
        self._clear_state()
        self._match_label.setText(STATUS_CANCELLED)

    def _on_result(self, job_id: int, result) -> None:  # type: ignore[no-untyped-def]
        if job_id != self._current_job_id:
            return

        self._offsets = result.offsets
        self._matches = [self._target.toPlainText()[s:e] for s, e in self._offsets]
        self._current_idx = 0 if self._offsets else -1

        self._update_labels(result)
        self._update_highlights()

    # ────────────────────────────────────────────────────────────────
    # UI Updates
    # ────────────────────────────────────────────────────────────────

    def _update_labels(self, result) -> None:  # type: ignore[no-untyped-def]
        if result.preview.startswith("⚠"):
            self._match_label.setText("expression error")
        elif result.preview == "invalid regex":
            self._match_label.setText("invalid regex")
        elif self._offsets:
            self._match_label.setText(f"{len(self._offsets)} matches")
        else:
            self._match_label.setText("no matches")

        if self._current_mode() is SearchMode.JSONPATH:
            self._jp_label.setText(result.preview)
            self._jp_label.setVisible(True)
            if self._filter_cb:
                try:
                    filtered = (
                        None
                        if not result.values
                        else result.values[0]
                        if len(result.values) == 1
                        else result.values
                    )
                    self._filter_cb(filtered)
                except Exception:
                    logger.exception("Failed to send JSONPath filter callback")

    def _update_highlights(self) -> None:
        if not self._offsets or self._current_idx < 0:
            self._target.setExtraSelections([])
            return

        doc = self._target.document()
        selections = []

        dim_fmt = QTextCharFormat()
        dim_fmt.setBackground(QColor("#ffd75f"))

        cur_fmt = QTextCharFormat()
        cur_fmt.setBackground(QColor("#e8a030"))

        radius = SEARCH_HIGHLIGHT_RADIUS
        start = max(0, self._current_idx - radius)
        end = min(len(self._offsets), self._current_idx + radius + 1)

        for i in range(start, end):
            s, e = self._offsets[i]
            cursor = QTextCursor(doc)
            cursor.setPosition(s)
            cursor.setPosition(e, QTextCursor.MoveMode.KeepAnchor)

            sel = QTextEdit.ExtraSelection()
            sel.cursor = cursor
            sel.format = cur_fmt if i == self._current_idx else dim_fmt
            selections.append(sel)

        self._target.setExtraSelections(selections)
        self._scroll_to_current_match()

    def _scroll_to_current_match(self) -> None:
        """Move viewport/caret to the active match (updates the QTextEdit cursor)."""
        if not self._offsets or self._current_idx < 0:
            return

        try:
            start, _ = self._offsets[self._current_idx]
            cursor = self._target.textCursor()
            cursor.setPosition(start)
            self._target.setTextCursor(cursor)
            self._target.ensureCursorVisible()
        except (IndexError, RuntimeError):
            logger.debug("Failed to scroll to current search match", exc_info=True)

    # ────────────────────────────────────────────────────────────────
    # Navigation
    # ────────────────────────────────────────────────────────────────

    def _find_next(self) -> None:
        if not self._offsets:
            return
        self._current_idx = (self._current_idx + 1) % len(self._offsets)
        self._update_highlights()

    def _find_prev(self) -> None:
        if not self._offsets:
            return
        self._current_idx = (self._current_idx - 1) % len(self._offsets)
        self._update_highlights()

    # ────────────────────────────────────────────────────────────────
    # Helpers
    # ────────────────────────────────────────────────────────────────

    def _clear_state(self) -> None:
        self._offsets.clear()
        self._matches.clear()
        self._current_idx = -1
        self._target.setExtraSelections([])
        self._jp_label.setVisible(False)
