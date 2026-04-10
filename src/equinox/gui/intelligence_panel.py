"""Intelligence panel — displays Response Intelligence findings."""
from __future__ import annotations

import json
import logging

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

# Severity → (icon, CSS color accessor)
_SEV_STYLE: dict[Severity, tuple[str, str]] = {
    Severity.CRITICAL: ("⛔", "ERROR"),
    Severity.WARNING:  ("⚠",  "WARNING"),
    Severity.INFO:     ("ℹ",  "INFO"),
}


class _FindingCard(QFrame):
    """A single finding rendered as a collapsible card."""

    def __init__(self, finding: Finding, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._finding = finding
        self._expanded = False
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setLineWidth(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(3)

        # ── Header row ────────────────────────────────────────────────
        header_row = QHBoxLayout()
        header_row.setSpacing(6)

        icon, color_attr = _SEV_STYLE.get(finding.severity, ("ℹ", "INFO"))
        color = getattr(Colors, color_attr, Colors.FG_MUTED)

        sev_label = QLabel(icon)
        sev_label.setFixedWidth(18)
        sev_label.setStyleSheet(f"font-size: 13px; color: {color};")
        header_row.addWidget(sev_label)

        title_label = QLabel(finding.title)
        title_label.setStyleSheet(f"font-weight: bold; color: {Colors.FG};")
        title_label.setWordWrap(True)
        header_row.addWidget(title_label, 1)

        sev_badge = QLabel(f" {finding.severity.value.upper()} ")
        sev_badge.setStyleSheet(
            f"color: white; background: {color}; border-radius: 3px; "
            f"font-size: 10px; padding: 1px 5px; font-weight: bold;"
        )
        sev_badge.setFixedHeight(18)
        header_row.addWidget(sev_badge)

        if finding.details:
            self._toggle_btn: QToolButton | None = QToolButton()
            self._toggle_btn.setText("▶")
            self._toggle_btn.setFixedSize(20, 20)
            self._toggle_btn.setStyleSheet(f"color: {Colors.FG_MUTED}; border: none;")
            self._toggle_btn.clicked.connect(self._toggle_details)
            header_row.addWidget(self._toggle_btn)
        else:
            self._toggle_btn = None

        layout.addLayout(header_row)

        # ── Description ───────────────────────────────────────────────
        desc = QLabel(finding.description)
        desc.setWordWrap(True)
        desc.setStyleSheet(f"color: {Colors.FG_MUTED}; padding-left: 24px;")
        layout.addWidget(desc)

        # ── Collapsible details ───────────────────────────────────────
        self._details_widget: QLabel | None = None
        if finding.details:
            self._details_widget = QLabel()
            self._details_widget.setFont(get_mono_font())
            self._details_widget.setWordWrap(True)
            self._details_widget.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            self._details_widget.setStyleSheet(
                f"color: {Colors.FG_MUTED}; background: {Colors.BG_ALT}; "
                f"padding: 6px; border-radius: 4px; margin-left: 24px; font-size: 11px;"
            )
            # json.dumps may raise for unusual types not covered by default=str;
            # fall back gracefully so the card still renders.
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
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)

        # ── Summary bar ───────────────────────────────────────────────
        self._summary_bar = QHBoxLayout()
        self._summary_label = QLabel("")
        self._summary_label.setStyleSheet(
            f"font-weight: bold; color: {Colors.FG}; padding: 2px 4px;"
        )
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
            self._summary_label.setText("✓ No issues found")
            self._summary_label.setStyleSheet(
                f"font-weight: bold; color: {Colors.SUCCESS}; padding: 2px 4px;"
            )
            self._placeholder.setText("✓ No issues found")
            self._placeholder.setStyleSheet(f"color: {Colors.SUCCESS}; font-weight: bold;")
            self._scroll.setVisible(False)
            self._placeholder.setVisible(True)
            # Still clear any previously displayed cards.
            self._scroll_content.setUpdatesEnabled(False)
            try:
                self._clear_cards()
            finally:
                self._scroll_content.setUpdatesEnabled(True)
            return

        # ── Build summary text ────────────────────────────────────────
        counts: dict[Severity, int] = {}
        for f in findings:
            counts[f.severity] = counts.get(f.severity, 0) + 1

        parts = []
        for sev in (Severity.CRITICAL, Severity.WARNING, Severity.INFO):
            c = counts.get(sev, 0)
            if c:
                icon, color_attr = _SEV_STYLE[sev]
                color = getattr(Colors, color_attr, Colors.FG_MUTED)
                parts.append(f'<span style="color:{color};">{icon} {c} {sev.value}</span>')
        self._summary_label.setText(
            f"  {len(findings)} finding(s):  " + "   ".join(parts)
        )
        self._summary_label.setTextFormat(Qt.TextFormat.RichText)
        self._summary_label.setStyleSheet(
            f"font-weight: bold; color: {Colors.FG}; padding: 2px 4px;"
        )

        # ── Group by category ─────────────────────────────────────────
        by_cat: dict[Category, list[Finding]] = {}
        for f in findings:
            by_cat.setdefault(f.category, []).append(f)

        # ── Rebuild cards (suppress per-card repaints) ────────────────
        self._scroll_content.setUpdatesEnabled(False)
        try:
            self._clear_cards()
            for cat in Category:
                cat_findings = by_cat.get(cat)
                if not cat_findings:
                    continue
                cat_label = QLabel(f"─── {cat.value} ───")
                cat_label.setStyleSheet(
                    f"font-weight: bold; color: {Colors.FG_MUTED}; "
                    f"padding: 4px 0 2px 0; font-size: 11px;"
                )
                # Insert before the trailing stretch (always at count-1).
                self._scroll_layout.insertWidget(
                    self._scroll_layout.count() - 1, cat_label
                )
                for finding in cat_findings:
                    self._scroll_layout.insertWidget(
                        self._scroll_layout.count() - 1, _FindingCard(finding)
                    )
        finally:
            self._scroll_content.setUpdatesEnabled(True)

        self._placeholder.setVisible(False)
        self._scroll.setVisible(True)

    def set_analyzing(self) -> None:
        """Show a 'running' state while analysis is in progress."""
        self._scroll_content.setUpdatesEnabled(False)
        try:
            self._clear_cards()
        finally:
            self._scroll_content.setUpdatesEnabled(True)
        self._summary_label.setText("⟳ Analyzing…")
        self._summary_label.setStyleSheet(
            f"font-weight: bold; color: {Colors.FG_MUTED}; padding: 2px 4px;"
        )
        self._placeholder.setText("⟳ Analyzing response…")
        self._placeholder.setStyleSheet(f"color: {Colors.FG_MUTED};")
        self._placeholder.setVisible(True)
        self._scroll.setVisible(False)

    def clear(self) -> None:
        """Reset to initial state."""
        self._scroll_content.setUpdatesEnabled(False)
        try:
            self._clear_cards()
        finally:
            self._scroll_content.setUpdatesEnabled(True)
        self._findings = []
        self._summary_label.setText("")
        self._placeholder.setText("Send a request to see analysis results.")
        self._placeholder.setStyleSheet("")
        self._placeholder.setVisible(True)
        self._scroll.setVisible(False)

    # ── Internal ──────────────────────────────────────────────────────────────

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

