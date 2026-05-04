"""Intelligence panel — displays Response Intelligence findings."""
from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from typing import Callable, Generator

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QFrame,
    QToolButton,
)
from PyQt6.QtCore import Qt

from equinox.core.response_intelligence.models import Category, Finding, Severity
from equinox.gui.theme import Colors, get_mono_font

__all__ = ["IntelligencePanel"]

logger = logging.getLogger(__name__)

# Severity → (icon, color_callable).
# Storing a callable (instead of a string attribute name resolved via getattr)
# keeps the Colors reference explicit and statically checkable.
_SEV_STYLE: dict[Severity, tuple[str, Callable[[], str]]] = {
    Severity.CRITICAL: ("⛔", lambda: Colors.ERROR),
    Severity.WARNING:  ("⚠",  lambda: Colors.WARNING),
    Severity.INFO:     ("ℹ",  lambda: Colors.INFO),
}

# Fallback used when a Severity value has no entry in _SEV_STYLE.
_SEV_STYLE_DEFAULT: tuple[str, Callable[[], str]] = ("ℹ", lambda: Colors.INFO)


class _FindingCard(QFrame):
    """A single finding rendered as a collapsible card."""

    def __init__(self, finding: Finding, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._finding = finding
        self._expanded = False
        self.setObjectName("intelCard")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setLineWidth(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(3)

        # ── Header row ────────────────────────────────────────────────
        header_row = QHBoxLayout()
        header_row.setSpacing(6)

        icon, _ = _SEV_STYLE.get(finding.severity, _SEV_STYLE_DEFAULT)

        sev_label = QLabel(icon)
        sev_label.setObjectName("intelSeverityIcon")
        sev_label.setProperty("severity", finding.severity.value)
        sev_label.setFixedWidth(18)
        header_row.addWidget(sev_label)

        title_label = QLabel(finding.title)
        title_label.setObjectName("intelTitle")
        title_label.setTextFormat(Qt.TextFormat.PlainText)
        title_label.setWordWrap(True)
        header_row.addWidget(title_label, 1)

        sev_badge = QLabel(f" {finding.severity.value.upper()} ")
        sev_badge.setObjectName("intelSeverityBadge")
        sev_badge.setProperty("severity", finding.severity.value)
        sev_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sev_badge.setFixedHeight(18)
        header_row.addWidget(sev_badge)

        if finding.details:
            self._toggle_btn: QToolButton | None = QToolButton()
            self._toggle_btn.setObjectName("intelToggle")
            self._toggle_btn.setText("▶")
            self._toggle_btn.setFixedSize(20, 20)
            self._toggle_btn.clicked.connect(self._toggle_details)
            header_row.addWidget(self._toggle_btn)
        else:
            self._toggle_btn = None

        layout.addLayout(header_row)

        # ── Description ───────────────────────────────────────────────
        desc = QLabel(finding.description)
        desc.setObjectName("intelDescription")
        desc.setTextFormat(Qt.TextFormat.PlainText)
        desc.setWordWrap(True)
        layout.addWidget(desc)

        if finding.recommendation:
            rec = QLabel(f"Suggested action: {finding.recommendation}")
            rec.setObjectName("intelRecommendation")
            rec.setTextFormat(Qt.TextFormat.PlainText)
            rec.setWordWrap(True)
            layout.addWidget(rec)

        # ── Collapsible details ───────────────────────────────────────
        self._details_widget: QLabel | None = None
        if finding.details:
            self._details_widget = QLabel()
            self._details_widget.setObjectName("intelDetails")
            self._details_widget.setFont(get_mono_font())
            self._details_widget.setWordWrap(True)
            self._details_widget.setTextFormat(Qt.TextFormat.PlainText)
            self._details_widget.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            try:
                detail_text = json.dumps(
                    finding.details, indent=2, ensure_ascii=False, default=str
                )
            except Exception as exc:
                logger.warning(
                    "Failed to serialise finding details for %r: %s",
                    finding.title,
                    exc,
                )
                detail_text = str(finding.details)
            self._details_widget.setText(detail_text)
            self._details_widget.setVisible(False)
            layout.addWidget(self._details_widget)

    def _toggle_details(self) -> None:
        self._expanded = not self._expanded
        if self._details_widget:
            self._details_widget.setVisible(self._expanded)
        if self._toggle_btn:
            self._toggle_btn.setText("▼" if self._expanded else "▶")


class IntelligencePanel(QWidget):
    """Scrollable panel that displays Response Intelligence findings.

    Call :meth:`display_findings` to populate and :meth:`clear` to reset.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._findings: list[Finding] = []
        self._init_ui()

    def _init_ui(self) -> None:
        self.setObjectName("intelligencePanel")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)

        # ── Summary bar ───────────────────────────────────────────────
        self._summary_bar = QHBoxLayout()
        self._summary_label = QLabel("")
        self._summary_label.setObjectName("intelSummary")
        self._summary_bar.addWidget(self._summary_label)
        self._summary_bar.addStretch()
        outer.addLayout(self._summary_bar)

        # ── Scroll area ───────────────────────────────────────────────
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)

        self._scroll_content = QWidget()
        self._scroll_layout = QVBoxLayout(self._scroll_content)
        self._scroll_layout.setContentsMargins(4, 4, 4, 4)
        self._scroll_layout.setSpacing(6)
        self._scroll_layout.addStretch()  # always kept as the last item

        self._scroll.setWidget(self._scroll_content)
        outer.addWidget(self._scroll, 1)

        # ── Placeholder ───────────────────────────────────────────────
        self._placeholder = QLabel("Send a request to see analysis results.")
        self._placeholder.setObjectName("mutedLabel")
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(self._placeholder)
        self._scroll.setVisible(False)

    # ── Public API ────────────────────────────────────────────────────────────

    def display_findings(self, findings: list[Finding]) -> None:
        """Populate the panel with *findings*.

        Screen updates on the scroll content are suppressed during the rebuild
        to avoid per-card repaint overhead.
        """
        self._findings = list(findings)  # defensive copy

        if not findings:
            self._set_summary("✓ No issues found", Colors.SUCCESS)
            self._set_placeholder("✓ No issues found", Colors.SUCCESS, bold=True)
            with self._suspend_card_updates():
                self._clear_cards()
            self._show_content(show_scroll=False)
            return

        self._set_summary(self._build_summary_html(findings), Colors.FG, rich_text=True)
        self._rebuild_cards(findings)
        self._show_content(show_scroll=True)

    def set_analyzing(self) -> None:
        """Show a 'running' state while analysis is in progress."""
        with self._suspend_card_updates():
            self._clear_cards()
        self._set_summary("⟳ Analyzing…", Colors.FG_MUTED)
        self._set_placeholder("⟳ Analyzing response…", Colors.FG_MUTED)
        self._show_content(show_scroll=False)

    def clear(self) -> None:
        """Reset to initial state."""
        with self._suspend_card_updates():
            self._clear_cards()
        self._findings = []
        self._set_summary("", Colors.FG)
        self._set_placeholder("Send a request to see analysis results.")
        self._show_content(show_scroll=False)

    # ── Private helpers ───────────────────────────────────────────────────────

    @contextmanager
    def _suspend_card_updates(self) -> Generator[None, None, None]:
        """Context manager that suppresses repaints on the scroll content widget.

        Using a context manager instead of inline try/finally blocks makes every
        call site one line and eliminates the risk of accidentally omitting the
        paired ``setUpdatesEnabled(True)`` call.
        """
        self._scroll_content.setUpdatesEnabled(False)
        try:
            yield
        finally:
            self._scroll_content.setUpdatesEnabled(True)

    def _set_summary(
        self, text: str, color: str, *, rich_text: bool = False
    ) -> None:
        """Update the summary bar label with *text* styled in *color*."""
        self._summary_label.setText(text)
        self._summary_label.setStyleSheet(f"color: {color};")
        self._summary_label.setTextFormat(
            Qt.TextFormat.RichText if rich_text else Qt.TextFormat.PlainText
        )

    def _set_placeholder(
        self, text: str, color: str = "", *, bold: bool = False
    ) -> None:
        """Update the placeholder label text and optional styling."""
        self._placeholder.setText(text)
        parts = []
        if color:
            parts.append(f"color: {color};")
        if bold:
            parts.append("font-weight: bold;")
        self._placeholder.setStyleSheet(" ".join(parts))

    def _show_content(self, *, show_scroll: bool) -> None:
        """Toggle between the scroll area (cards) and the placeholder label."""
        self._scroll.setVisible(show_scroll)
        self._placeholder.setVisible(not show_scroll)

    @staticmethod
    def _build_summary_html(findings: list[Finding]) -> str:
        """Return an HTML string summarising finding counts by severity."""
        counts: dict[Severity, int] = {}
        for f in findings:
            counts[f.severity] = counts.get(f.severity, 0) + 1

        parts = []
        for sev in (Severity.CRITICAL, Severity.WARNING, Severity.INFO):
            c = counts.get(sev, 0)
            if c:
                icon, color_fn = _SEV_STYLE.get(sev, _SEV_STYLE_DEFAULT)
                parts.append(
                    f'<span style="color:{color_fn()};">{icon} {c} {sev.value}</span>'
                )
        return f"  {len(findings)} finding(s):  " + "   ".join(parts)

    def _rebuild_cards(self, findings: list[Finding]) -> None:
        """Clear existing cards and insert new ones grouped by category."""
        by_cat: dict[Category, list[Finding]] = {}
        for f in findings:
            by_cat.setdefault(f.category, []).append(f)

        with self._suspend_card_updates():
            self._clear_cards()
            for cat in Category:
                cat_findings = by_cat.get(cat)
                if not cat_findings:
                    continue
                cat_label = QLabel(f"─── {cat.value} ───")
                cat_label.setObjectName("intelCategory")
                # Insert before the trailing stretch (always at count - 1).
                self._scroll_layout.insertWidget(
                    self._scroll_layout.count() - 1, cat_label
                )
                for finding in cat_findings:
                    self._scroll_layout.insertWidget(
                        self._scroll_layout.count() - 1, _FindingCard(finding)
                    )

    def _clear_cards(self) -> None:
        """Remove all finding cards and category labels from the scroll layout.

        Items are removed from the back (second-to-last position) to avoid
        shifting the entire list on each removal — the trailing stretch item
        is always kept at index ``count - 1``.
        """
        while self._scroll_layout.count() > 1:  # keep the trailing stretch
            item = self._scroll_layout.takeAt(self._scroll_layout.count() - 2)
            w = item.widget()
            if w:
                w.deleteLater()

