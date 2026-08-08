from __future__ import annotations

import difflib
from typing import Any

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QTextCharFormat
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QSplitter,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from equinox.gui.theme import Colors, get_mono_font


class HistoryDiffDialog(QDialog):
    """Side-by-side diff of two history entries (request and response)."""

    def __init__(
        self,
        entry_a: dict[str, Any],
        entry_b: dict[str, Any],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._entry_a = entry_a
        self._entry_b = entry_b
        self.setWindowTitle(
            f"Compare  [{entry_a.get('method')} {entry_a.get('url', '')[:40]}]  "
            f"vs  [{entry_b.get('method')} {entry_b.get('url', '')[:40]}]",
        )
        self.setMinimumSize(980, 640)
        self._init_ui()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)

        tabs = QTabWidget()
        tabs.addTab(
            self._make_split_widget(
                self._format_request(self._entry_a),
                self._format_request(self._entry_b),
                label_a=f"Entry #{self._entry_a.get('id')}",
                label_b=f"Entry #{self._entry_b.get('id')}",
            ),
            "Request",
        )
        tabs.addTab(
            self._make_split_widget(
                self._format_response(self._entry_a),
                self._format_response(self._entry_b),
                label_a=f"Entry #{self._entry_a.get('id')}",
                label_b=f"Entry #{self._entry_b.get('id')}",
            ),
            "Response",
        )
        tabs.addTab(self._make_unified_diff_widget(), "Unified Diff (response body)")

        layout.addWidget(tabs, 1)

        close_btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_btns.rejected.connect(self.reject)
        layout.addWidget(close_btns)

    # ── Formatters ────────────────────────────────────────────────────────────

    @staticmethod
    def _format_request(entry: dict[str, Any]) -> str:
        lines = [
            f"{entry.get('method', '?')} {entry.get('url', '?')}",
            f"Timestamp : {entry.get('executed_at', '?')}",
            "",
            "── Headers ──",
        ]
        for k, v in (entry.get("request_headers") or {}).items():
            lines.append(f"  {k}: {v}")
        lines += ["", "── Body ──", entry.get("request_body") or "(none)"]
        return "\n".join(lines)

    @staticmethod
    def _format_response(entry: dict[str, Any]) -> str:
        lines = [
            f"Status  : {entry.get('status_code', '?')} {entry.get('reason', '')}",
            f"Elapsed : {int((entry.get('elapsed') or 0) * 1000)} ms",
            "",
            "── Headers ──",
        ]
        for k, v in (entry.get("response_headers") or {}).items():
            lines.append(f"  {k}: {v}")
        lines += ["", "── Body ──", entry.get("response_body") or "(none)"]
        return "\n".join(lines)

    # ── Widgets ───────────────────────────────────────────────────────────────

    def _make_split_widget(
        self,
        text_a: str,
        text_b: str,
        label_a: str = "A",
        label_b: str = "B",
    ) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(4, 4, 4, 4)

        hdr = QHBoxLayout()
        for lbl in (label_a, label_b):
            lbl_widget = QLabel(f"<b>{lbl}</b>")
            lbl_widget.setAlignment(Qt.AlignmentFlag.AlignCenter)
            hdr.addWidget(lbl_widget, 1)
        lay.addLayout(hdr)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        for text in (text_a, text_b):
            te = QTextEdit()
            te.setReadOnly(True)
            te.setFont(get_mono_font())
            te.setPlainText(text)
            splitter.addWidget(te)
        splitter.setSizes([490, 490])
        lay.addWidget(splitter, 1)
        return w

    def _make_unified_diff_widget(self) -> QWidget:
        body_a = (self._entry_a.get("response_body") or "").splitlines(keepends=True)
        body_b = (self._entry_b.get("response_body") or "").splitlines(keepends=True)
        diff_lines = list(
            difflib.unified_diff(
                body_a,
                body_b,
                fromfile=f"Entry #{self._entry_a.get('id')}",
                tofile=f"Entry #{self._entry_b.get('id')}",
                lineterm="",
            ),
        )

        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(4, 4, 4, 4)

        te = QTextEdit()
        te.setReadOnly(True)
        te.setFont(get_mono_font())

        if not diff_lines:
            te.setPlainText("(no differences in response bodies)")
        else:
            # Use theme foreground colours so the diff is readable in both
            # light and dark modes (background tinting is not theme-aware).
            fmt_add = QTextCharFormat()
            fmt_add.setForeground(QColor(Colors.SUCCESS))
            fmt_rem = QTextCharFormat()
            fmt_rem.setForeground(QColor(Colors.ERROR))
            fmt_hdr = QTextCharFormat()
            fmt_hdr.setForeground(QColor(Colors.INFO))
            fmt_def = QTextCharFormat()

            cursor = te.textCursor()
            for line in diff_lines:
                if line.startswith("+++") or line.startswith("---"):
                    cursor.insertText(line + "\n", fmt_hdr)
                elif line.startswith("+"):
                    cursor.insertText(line + "\n", fmt_add)
                elif line.startswith("-"):
                    cursor.insertText(line + "\n", fmt_rem)
                else:
                    cursor.insertText(line + "\n", fmt_def)

        lay.addWidget(te, 1)
        return w
