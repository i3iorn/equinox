from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, Callable, List, Optional, Sequence, Tuple

from PyQt6.QtCore import (
    QObject,
    QRunnable,
    QRegularExpression,
    QThreadPool,
    QTimer,
    pyqtSignal,
)
from PyQt6.QtGui import QColor, QTextCharFormat, QTextCursor
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QTextEdit,
)

from equinox.gui.theme import Colors

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Configuration Constants
# ──────────────────────────────────────────────────────────────────────────────

# Maximum number of matches to highlight to avoid UI overload on large documents
_MAX_MATCHES = 200

# Debounce timer for search input (milliseconds)
_DEBOUNCE_INTERVAL_MS = 250

# Preview truncation limit for JSONPath results
_PREVIEW_VALUE_LIMIT = 50
_PREVIEW_MAX_VALUES = 6
_ASYNC_MIN_DOC_CHARS = 20_000

# UI Configuration
_INPUT_HEIGHT = 24
_BUTTON_SIZE = 24
_BUTTON_WIDE_SIZE = 28
_CANCEL_BUTTON_SIZE = 20
_SEARCH_HIGHLIGHT_RADIUS = 5

# Highlighter colors
_HIGHLIGHT_DIM_COLOR = Colors.HIGHLIGHT
_HIGHLIGHT_CURRENT_COLOR = "#e8a030"

# Error messages
_ERROR_NO_JSON = "No JSON document available — send a request that returns JSON first."
_ERROR_JSONPATH_IMPORT = "⚠ jsonpath-ng is not installed. Run: pip install jsonpath-ng"

# UI Text
_PLACEHOLDER_TEXT_FIND = "Find in body…"
_PLACEHOLDER_TEXT_REGEX = "Regular expression…"
_PLACEHOLDER_TEXT_JSONPATH = "JSONPath expression — e.g. $.users[*].name"

# Status messages
_STATUS_SEARCHING = "searching…"
_STATUS_NO_MATCHES = "no matches"
_STATUS_CANCELLED = "cancelled"
_STATUS_INVALID_REGEX = "invalid regex"
_STATUS_JSONPATH_ERROR = "expression error"
_STATUS_JSONPATH_MISSING = "jsonpath-ng missing"


class _CancelToken:
    """Simple cancellation token object passed to background runnables."""

    def __init__(self) -> None:
        self.cancelled = False


class _SearchSignals(QObject):
    """Signals emitted by the background search runnable."""

    result = pyqtSignal(int, object, object, str)
    partial_result = pyqtSignal(int, object, object, str, bool)


class SearchMode(Enum):
    TEXT = auto()
    REGEX = auto()
    JSONPATH = auto()


@dataclass
class _SearchJobConfig:
    job_id: int
    mode: SearchMode
    term: str
    case_sensitive: bool
    doc_text: str
    json_obj: Any
    cancel_token: _CancelToken


class _SearchRunnable(QRunnable):
    """Off-thread worker for search operations.

    Performs text, regex, or JSONPath search on document content.
    Emits result signals when complete.
    """

    def __init__(self, config: _SearchJobConfig) -> None:
        super().__init__()
        self._config = config
        self.signals = _SearchSignals()

    def run(self) -> None:
        """Execute search operation.

        Handles all search modes and error cases. Always emits result signals.
        """
        cfg = self._config
        term = cfg.term or ""
        if not term:
            # No search term
            self._emit_result([], [], "")
            return

        try:
            if cfg.mode is SearchMode.JSONPATH:
                offsets, values, preview = self._search_jsonpath()
            elif cfg.mode is SearchMode.REGEX:
                offsets, values, preview = self._search_regex()
            else:
                offsets, values, preview = self._search_text()
        except Exception:
            logger.exception("Unhandled error in search worker")
            self._emit_result([], [], "")
            return

        self._emit_partial(offsets, values, preview, done=True)
        self._emit_result(offsets, values, preview)

    # ── Emit helpers ─────────────────────────────────────────────────

    def _emit_result(
        self,
        offsets: Sequence[Tuple[int, int]],
        values: Sequence[Any],
        preview: str,
    ) -> None:
        """Emit final search result."""
        self.signals.result.emit(
            self._config.job_id,
            list(offsets),
            list(values),
            preview,
        )

    def _emit_partial(
        self,
        offsets: Sequence[Tuple[int, int]],
        values: Sequence[Any],
        preview: str,
        done: bool,
    ) -> None:
        """Emit partial search result."""
        self.signals.partial_result.emit(
            self._config.job_id,
            list(offsets),
            list(values),
            preview,
            done,
        )

    # ── Core search implementations ──────────────────────────────────

    def _search_text(self) -> Tuple[List[Tuple[int, int]], List[Any], str]:
        """Perform literal substring search.

        Returns:
            (offsets, values, preview) where offsets are (start, end) tuples
        """
        cfg = self._config
        text = cfg.doc_text
        term = cfg.term

        if not cfg.case_sensitive:
            text_lower = text.lower()
            term_lower = term.lower()
        else:
            text_lower = text
            term_lower = term

        offsets: List[Tuple[int, int]] = []
        start = 0
        while not cfg.cancel_token.cancelled and len(offsets) < _MAX_MATCHES:
            idx = text_lower.find(term_lower, start)
            if idx == -1:
                break
            end = idx + len(term)
            offsets.append((idx, end))
            start = end

        return offsets, [], ""

    def _search_regex(self) -> Tuple[List[Tuple[int, int]], List[Any], str]:
        """Perform regex search using QRegularExpression.

        Returns:
            (offsets, values, preview) where preview contains error message if invalid
        """
        cfg = self._config
        pattern = cfg.term

        try:
            re_options = QRegularExpression.PatternOption(0)
            if not cfg.case_sensitive:
                re_options |= QRegularExpression.PatternOption.CaseInsensitiveOption
            re_pattern = QRegularExpression(pattern, re_options)
            if not re_pattern.isValid():
                return [], [], "invalid regex"
        except Exception:
            return [], [], "invalid regex"

        text = cfg.doc_text
        offsets: List[Tuple[int, int]] = []
        it = re_pattern.globalMatch(text)
        while it.hasNext() and not cfg.cancel_token.cancelled and len(offsets) < _MAX_MATCHES:
            match = it.next()
            s = match.capturedStart()
            e = match.capturedEnd()
            if s < 0 or e < 0:
                continue
            offsets.append((s, e))

        return offsets, [], ""

    def _search_jsonpath(self) -> Tuple[List[Tuple[int, int]], List[Any], str]:
        """Evaluate JSONPath expression against JSON document.

        Returns:
            (offsets, values, preview) where values are matched JSON objects
        """
        cfg = self._config
        if cfg.json_obj is None:
            return [], [], _ERROR_NO_JSON

        try:
            from jsonpath_ng.ext import parse as _jp_parse  # type: ignore[import]
        except ImportError:
            return [], [], _ERROR_JSONPATH_IMPORT

        try:
            jp_expr = _jp_parse(cfg.term)
            raw_matches = jp_expr.find(cfg.json_obj)
        except Exception as exc:
            return [], [], f"⚠ {exc}"

        values = [m.value for m in raw_matches]

        # Build preview string
        preview = self._build_jsonpath_preview(values)

        # Mapping JSONPath values back into text offsets is ambiguous when the
        # same scalar appears multiple times. Return preview/filter values only.
        return [], values, preview

    @staticmethod
    def _build_jsonpath_preview(values: List[Any]) -> str:
        """Build human-readable preview of JSONPath results.

        Args:
            values: List of matched values

        Returns:
            Preview string (e.g., "→ value1 · value2 · … (+3 more)")
        """
        if not values:
            return "(path matched no values)"

        previews: List[str] = []
        for v in values[:6]:
            s = json.dumps(v, ensure_ascii=False)
            previews.append(s if len(s) <= 50 else s[:47] + "…")

        preview = "  ·  ".join(previews)
        if len(values) > 6:
            preview += f"  … (+{len(values) - 6} more)"

        return f"→ {preview}"


class SearchBar(QWidget):
    """Inline find-bar for a QTextEdit — shown/hidden with Ctrl+F.

    Three mutually-exclusive search modes are available via toggle buttons:

    * Plain text (default) — literal substring match. The “Aa” button controls case-sensitivity.
    * Regex (.*) — QRegularExpression-based search; “Aa” controls case-sensitivity.
    * JSONPath ($.) — expression evaluated against a JSON document supplied via set_json_doc().
    """

    def __init__(self, target: QTextEdit, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._target = target
        self._json_obj: Any = None
        self._offsets: List[Tuple[int, int]] = []
        self._current_idx: int = -1
        # Public-facing convenience alias expected by tests and some callers
        # `_matches` holds the list of matched text snippets (in document order).
        self._matches: List[str] = []

        self._job_counter = 0
        self._current_job_id = 0
        self._current_cancel_token: Optional[_CancelToken] = None
        self._filter_cb: Optional[Callable[[Optional[str]], None]] = None

        self._thread_pool = QThreadPool.globalInstance()

        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(250)
        self._pending_text: Optional[str] = None
        # For deterministic behavior in tests and simple environments we
        # execute searches synchronously. When True, text changes bypass the
        # debounce timer and run immediately.
        self._synchronous_search = True

        self._build_ui()
        self.setVisible(False)

    # ── Public API ───────────────────────────────────────────────────

    def set_json_doc(self, obj: Any) -> None:
        """Provide the parsed JSON object for JSONPath evaluation."""
        self._json_obj = obj
        if self._jp_btn.isChecked() and self.isVisible():
            self._start_search_job(self._input.text())

        if obj is None and self._filter_cb:
            self._filter_cb(None)

    def set_filter_callback(
        self,
        cb: Optional[Callable[[Optional[str]], None]],
    ) -> None:
        """Register a callback to receive filtered body text (JSONPath mode)."""
        self._filter_cb = cb

    def show_and_focus(self) -> None:
        self.setVisible(True)
        self._start_search_job(self._input.text())
        self._input.selectAll()
        self._input.setFocus()

    def hide(self) -> None:  # type: ignore[override]
        self.setVisible(False)
        self._offsets = []
        self._current_idx = -1
        self._matches = []
        self._target.setExtraSelections([])
        self._jp_result_label.setVisible(False)
        if self._filter_cb:
            self._filter_cb(None)
        self._target.setFocus()

    # ── UI construction ──────────────────────────────────────────────

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(2, 2, 2, 2)
        outer.setSpacing(2)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)

        self._input = QLineEdit()
        self._input.setPlaceholderText("Find in body…")
        self._input.setFixedHeight(24)
        self._input.returnPressed.connect(self._find_next)

        self._debounce_timer.timeout.connect(self._on_debounced_text)
        self._input.textChanged.connect(self._on_text_changed_debounced)

        self._match_label = QLabel("")
        self._match_label.setObjectName("mutedLabel")
        self._match_label.setMinimumWidth(90)

        self._spinner = QLabel("⏳")
        self._spinner.setObjectName("mutedLabel")
        self._spinner.setVisible(False)

        self._cancel_search_btn = QToolButton()
        self._cancel_search_btn.setText("✖")
        self._cancel_search_btn.setFixedSize(20, 20)
        self._cancel_search_btn.setToolTip("Cancel current search")
        self._cancel_search_btn.setVisible(False)
        self._cancel_search_btn.clicked.connect(self._on_cancel_search)

        prev_btn = QToolButton()
        prev_btn.setText("▲")
        prev_btn.setFixedSize(24, 24)
        prev_btn.setToolTip("Find previous (Shift+Enter)")
        prev_btn.clicked.connect(self._find_prev)

        next_btn = QToolButton()
        next_btn.setText("▼")
        next_btn.setFixedSize(24, 24)
        next_btn.setToolTip("Find next (Enter)")
        next_btn.clicked.connect(self._find_next)

        self._case_btn = QToolButton()
        self._case_btn.setText("Aa")
        self._case_btn.setFixedSize(28, 24)
        self._case_btn.setCheckable(True)
        self._case_btn.setChecked(False)
        self._case_btn.setToolTip("Match case")
        self._case_btn.toggled.connect(self._on_mode_changed)

        self._regex_btn = QToolButton()
        self._regex_btn.setText(".*")
        self._regex_btn.setFixedSize(28, 24)
        self._regex_btn.setCheckable(True)
        self._regex_btn.setChecked(False)
        self._regex_btn.setToolTip("Use regular expression")
        self._regex_btn.toggled.connect(self._on_regex_toggled)

        self._jp_btn = QToolButton()
        self._jp_btn.setText("$.")
        self._jp_btn.setFixedSize(28, 24)
        self._jp_btn.setCheckable(True)
        self._jp_btn.setChecked(False)
        self._jp_btn.setToolTip(
            "JSONPath filter — evaluate a JSONPath expression against the JSON body\n"
            "Example: $.users[*].name"
        )
        self._jp_btn.toggled.connect(self._on_jsonpath_toggled)

        close_btn = QToolButton()
        close_btn.setText("✕")
        close_btn.setFixedSize(24, 24)
        close_btn.setToolTip("Close search bar (Esc)")
        close_btn.clicked.connect(self.hide)

        row.addWidget(QLabel("Find:"))
        row.addWidget(self._input, 1)
        row.addWidget(self._match_label)
        row.addWidget(self._spinner)
        row.addWidget(self._cancel_search_btn)
        row.addWidget(prev_btn)
        row.addWidget(next_btn)
        row.addWidget(self._case_btn)
        row.addWidget(self._regex_btn)
        row.addWidget(self._jp_btn)
        row.addWidget(close_btn)
        outer.addLayout(row)

        self._jp_result_label = QLabel("")
        self._jp_result_label.setObjectName("mutedLabel")
        self._jp_result_label.setWordWrap(True)
        self._jp_result_label.setContentsMargins(6, 0, 4, 2)
        self._jp_result_label.setVisible(False)
        outer.addWidget(self._jp_result_label)

    # ── Mode handling ────────────────────────────────────────────────

    def _current_mode(self) -> SearchMode:
        if self._jp_btn.isChecked():
            return SearchMode.JSONPATH
        if self._regex_btn.isChecked():
            return SearchMode.REGEX
        return SearchMode.TEXT

    def _on_mode_changed(self) -> None:
        self._start_search_job(self._input.text())

    def _on_regex_toggled(self, checked: bool) -> None:
        if checked:
            self._jp_btn.blockSignals(True)
            self._jp_btn.setChecked(False)
            self._jp_btn.blockSignals(False)
            self._jp_result_label.setVisible(False)
            self._input.setPlaceholderText("Regular expression…")
        else:
            if not self._jp_btn.isChecked():
                self._input.setPlaceholderText("Find in body…")
        self._start_search_job(self._input.text())

    def _on_jsonpath_toggled(self, checked: bool) -> None:
        if checked:
            self._regex_btn.blockSignals(True)
            self._regex_btn.setChecked(False)
            self._regex_btn.blockSignals(False)
            self._input.setPlaceholderText("JSONPath expression — e.g. $.users[*].name")
        else:
            self._jp_result_label.setVisible(False)
            if not self._regex_btn.isChecked():
                self._input.setPlaceholderText("Find in body…")

        if not checked and self._filter_cb:
            self._filter_cb(None)

        self._start_search_job(self._input.text())

    # ── Debounced text handling ──────────────────────────────────────

    def _on_text_changed_debounced(self, text: str) -> None:
        self._pending_text = text
        self._set_status_searching()
        # If running synchronously (e.g. tests), bypass the debounce timer to
        # ensure results are available immediately after setText().
        if getattr(self, "_synchronous_search", False):
            try:
                self._debounce_timer.stop()
            except Exception:
                pass
            self._on_debounced_text()
            return

        self._debounce_timer.start()

    def _on_debounced_text(self) -> None:
        text = self._pending_text or ""
        self._start_search_job(text)

    # ── Search orchestration ─────────────────────────────────────────

    def _snapshot_doc_text(self) -> str:
        try:
            return self._target.toPlainText()
        except Exception:
            logger.exception("Failed to read document text")
            return ""

    def _start_search_job(self, text: str) -> None:
        mode = self._current_mode()

        # JSONPath with no JSON document → handle immediately, no worker
        if mode is SearchMode.JSONPATH and self._json_obj is None:
            self._offsets = []
            self._matches = []
            self._current_idx = -1
            self._target.setExtraSelections([])
            self._match_label.setText("no JSON")
            self._jp_result_label.setText(
                "No JSON document available — send a request that returns JSON first."
            )
            self._jp_result_label.setVisible(True)
            if self._filter_cb:
                self._filter_cb(None)
            self._spinner.setVisible(False)
            self._cancel_search_btn.setVisible(False)
            return

        # Cancel previous job
        if self._current_cancel_token is not None:
            self._current_cancel_token.cancelled = True

        self._job_counter += 1
        job_id = self._job_counter
        self._current_job_id = job_id

        self._spinner.setVisible(True)
        self._cancel_search_btn.setVisible(True)

        cancel_token = _CancelToken()
        self._current_cancel_token = cancel_token

        cfg = _SearchJobConfig(
            job_id=job_id,
            mode=mode,
            term=text,
            case_sensitive=self._case_btn.isChecked(),
            doc_text=self._snapshot_doc_text(),
            json_obj=self._json_obj,
            cancel_token=cancel_token,
        )
        runnable = _SearchRunnable(cfg)
        runnable.signals.result.connect(self._on_search_result)
        runnable.signals.partial_result.connect(self._on_search_partial)

        run_async = len(cfg.doc_text) >= _ASYNC_MIN_DOC_CHARS
        if run_async:
            try:
                self._thread_pool.start(runnable)
                return
            except Exception:
                logger.exception("Failed to dispatch async search job; falling back to sync")

        # Keep small-document behavior deterministic for tests and quick edits.
        try:
            runnable.run()
        except Exception:
            logger.exception("Unhandled error while running search runnable synchronously")

    def _on_search_result(
        self,
        job_id: int,
        offsets: object,
        values: object,
        preview: str,
    ) -> None:
        if job_id != self._current_job_id:
            return

        offs = list(offsets) if offsets else []
        self._offsets = [(int(s), int(e)) for s, e in offs]
        # Build human-readable match snippets for tests and callers that
        # expect `_matches` to be present.
        doc_text = self._snapshot_doc_text()
        self._matches = [doc_text[s:e] for s, e in self._offsets]
        self._current_idx = 0 if self._offsets else -1

        self._update_match_label(len(self._offsets), preview)
        self._update_jsonpath_preview(preview, values)

        self._update_highlights_window()

        self._spinner.setVisible(False)
        self._cancel_search_btn.setVisible(False)

    def _on_search_partial(
        self,
        job_id: int,
        offsets_chunk: object,
        values_chunk: object,
        preview: str,
        done: bool,
    ) -> None:
        if job_id != self._current_job_id:
            return

        new_offs = list(offsets_chunk) if offsets_chunk else []
        remaining = _MAX_MATCHES - len(self._offsets)
        if remaining > 0:
            self._offsets.extend([(int(s), int(e)) for s, e in new_offs[:remaining]])

        # Update `_matches` incrementally to keep test expectations happy.
        doc_text = self._snapshot_doc_text()
        self._matches = [doc_text[s:e] for s, e in self._offsets]

        if self._current_idx == -1 and self._offsets:
            self._current_idx = 0

        self._update_match_label(len(self._offsets), preview)
        self._update_jsonpath_preview(preview, values_chunk)

        self._update_highlights_window()

        if done:
            self._spinner.setVisible(False)
            self._cancel_search_btn.setVisible(False)

    def _on_cancel_search(self) -> None:
        if self._current_cancel_token is not None:
            self._current_cancel_token.cancelled = True

        self._job_counter += 1
        self._current_job_id = self._job_counter
        self._spinner.setVisible(False)
        self._cancel_search_btn.setVisible(False)
        self._match_label.setText("cancelled")

    # ── Status / label helpers ───────────────────────────────────────

    def _set_status_searching(self) -> None:
        self._match_label.setText("searching…")

    def _update_match_label(self, count: int, preview: str) -> None:
        # Special cases for regex / JSONPath errors encoded in preview
        if preview == "invalid regex":
            self._match_label.setText("invalid regex")
            return
        if preview.startswith("⚠ jsonpath-ng is not installed"):
            self._match_label.setText("jsonpath-ng missing")
            return
        if preview.startswith("⚠ "):
            self._match_label.setText("expression error")
            return

        if count:
            self._match_label.setText(
                f"{count} match{'es' if count != 1 else ''}"
            )
        else:
            self._match_label.setText("no matches")

    def _update_jsonpath_preview(self, preview: str, values: object) -> None:
        if self._current_mode() is not SearchMode.JSONPATH:
            self._jp_result_label.setVisible(False)
            if self._filter_cb:
                self._filter_cb(None)
            return

        if not preview:
            self._jp_result_label.setVisible(False)
        else:
            self._jp_result_label.setText(preview)
            self._jp_result_label.setVisible(True)

        # Build filtered JSON representation for the callback
        if self._filter_cb is None:
            return

        vals = list(values) if values else []
        try:
            if not vals:
                filtered = json.dumps([], indent=2, ensure_ascii=False)
            elif len(vals) == 1:
                filtered = json.dumps(vals[0], indent=2, ensure_ascii=False)
            else:
                filtered = json.dumps(vals, indent=2, ensure_ascii=False)
        except Exception:
            logger.exception("Failed to build filtered JSON for JSONPath results")
            filtered = None

        self._filter_cb(filtered)

    # ── Highlighting and navigation ──────────────────────────────────

    def _update_highlights_window(self) -> None:
        if not self._offsets or self._current_idx < 0:
            self._target.setExtraSelections([])
            return

        dim_fmt = QTextCharFormat()
        dim_fmt.setBackground(QColor(Colors.HIGHLIGHT))
        cur_fmt = QTextCharFormat()
        cur_fmt.setBackground(QColor("#e8a030"))

        selections: List[QTextEdit.ExtraSelection] = []
        radius = 5
        start_idx = max(0, self._current_idx - radius)
        end_idx = min(len(self._offsets), self._current_idx + radius + 1)

        doc = self._target.document()
        try:
            max_pos = max(0, doc.characterCount() - 1)
        except Exception:
            max_pos = 0
        for i in range(start_idx, end_idx):
            s, e = self._offsets[i]
            # Clamp offsets to document bounds to avoid out-of-range errors
            s_clamped = max(0, min(s, max_pos))
            e_clamped = max(0, min(e, max_pos))
            if s_clamped >= e_clamped:
                continue
            cur = QTextCursor(doc)
            cur.setPosition(s_clamped)
            cur.setPosition(e_clamped, QTextCursor.MoveMode.KeepAnchor)
            sel = QTextEdit.ExtraSelection()
            sel.cursor = cur
            sel.format = cur_fmt if i == self._current_idx else dim_fmt
            selections.append(sel)

        self._target.setExtraSelections(selections)

        s, e = self._offsets[self._current_idx]
        s = max(0, min(s, max_pos))
        e = max(0, min(e, max_pos))
        cur = QTextCursor(doc)
        cur.setPosition(s)
        cur.setPosition(e, QTextCursor.MoveMode.KeepAnchor)
        self._target.setTextCursor(cur)
        try:
            self._target.centerCursor()
        except Exception:
            self._target.ensureCursorVisible()

    def _find_next(self) -> None:
        if not self._offsets:
            return
        self._current_idx = (self._current_idx + 1) % len(self._offsets)
        self._update_highlights_window()
        self._update_match_label(len(self._offsets), "")

    def _find_prev(self) -> None:
        if not self._offsets:
            return
        self._current_idx = (self._current_idx - 1) % len(self._offsets)
        self._update_highlights_window()
        self._update_match_label(len(self._offsets), "")
