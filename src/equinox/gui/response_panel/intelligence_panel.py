"""Intelligence panel — displays Response Intelligence findings."""

from __future__ import annotations

import json
import logging
from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from equinox.core.response_intelligence import Category, Finding, Severity
from equinox.gui.theme import Colors, get_mono_font

__all__ = ["IntelligencePanel"]

from equinox.gui.ui_common import get_gui_settings

logger = logging.getLogger(__name__)

_AUDIT_TAIL_BYTES = 256 * 1024
_AUDIT_MAX_LINES = 400

# Severity → (icon, color_callable).
# Storing a callable (instead of a string attribute name resolved via getattr)
# keeps the Colors reference explicit and statically checkable.
_SEV_STYLE: dict[Severity, tuple[str, Callable[[], str]]] = {
    Severity.CRITICAL: ("⛔", lambda: Colors.ERROR),
    Severity.WARNING: ("⚠", lambda: Colors.WARNING),
    Severity.INFO: ("ℹ", lambda: Colors.INFO),
}

# Fallback used when a Severity value has no entry in _SEV_STYLE.
_SEV_STYLE_DEFAULT: tuple[str, Callable[[], str]] = ("ℹ", lambda: Colors.INFO)
_KEY_MUTED_FINDINGS = "intelligence/muted_findings"


def _finding_key(finding: Finding) -> str:
    """Return a stable key used for muting a finding class."""
    return finding.analyzer_id or finding.title


def _missing_headers_template(missing: list[dict]) -> str:
    """Build a copy/paste security header template from missing-header findings."""
    defaults = {
        "strict-transport-security": "max-age=31536000; includeSubDomains",
        "content-security-policy": "default-src 'self'",
        "x-content-type-options": "nosniff",
        "x-frame-options": "DENY",
        "permissions-policy": "camera=(), microphone=(), geolocation=()",
        "referrer-policy": "strict-origin-when-cross-origin",
    }
    lines = []
    for row in missing:
        name = str(row.get("header") or "").strip().lower()
        if not name:
            continue
        lines.append(f"{name}: {defaults.get(name, '<set-value>')}")
    return "\n".join(lines)


class _FindingCard(QFrame):
    """A single finding rendered as a collapsible card."""

    def __init__(
        self,
        finding: Finding,
        on_apply: Callable[[Finding], None] | None = None,
        on_mute: Callable[[Finding], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._finding = finding
        self._on_apply = on_apply
        self._on_mute = on_mute
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

        # ── Actions ─────────────────────────────────────────────────────
        actions = QHBoxLayout()
        actions.setSpacing(6)

        copy_fix = QPushButton("Copy Fix")
        copy_fix.setObjectName("intelActionBtn")
        copy_fix.clicked.connect(self._copy_fix)
        actions.addWidget(copy_fix)

        apply_btn = QPushButton("Apply")
        apply_btn.setObjectName("intelActionBtn")
        apply_btn.clicked.connect(self._apply_fix)
        actions.addWidget(apply_btn)

        task_btn = QPushButton("Copy Task")
        task_btn.setObjectName("intelActionBtn")
        task_btn.clicked.connect(self._copy_task)
        actions.addWidget(task_btn)

        mute_btn = QPushButton("Mute 7d")
        mute_btn.setObjectName("intelActionBtn")
        mute_btn.clicked.connect(self._mute_finding)
        actions.addWidget(mute_btn)

        actions.addStretch()
        layout.addLayout(actions)

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
                detail_text = json.dumps(finding.details, indent=2, ensure_ascii=False, default=str)
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

    def _copy_fix(self) -> None:
        text = self._finding.recommendation or self._finding.description
        QGuiApplication.clipboard().setText(text)

    def _copy_task(self) -> None:
        task = (
            f"- [ ] [{self._finding.severity.value.upper()}] {self._finding.title}\n"
            f"  - Finding: {self._finding.description}\n"
            f"  - Action: {self._finding.recommendation or 'Investigate and remediate'}\n"
            f"  - Analyzer: {self._finding.analyzer_id}"
        )
        QGuiApplication.clipboard().setText(task)

    def _apply_fix(self) -> None:
        if self._on_apply is not None:
            self._on_apply(self._finding)

    def _mute_finding(self) -> None:
        if self._on_mute is not None:
            self._on_mute(self._finding)


class IntelligencePanel(QWidget):
    """Scrollable panel that displays Response Intelligence findings.

    Call :meth:`display_findings` to populate and :meth:`clear` to reset.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._findings: list[Finding] = []
        self._settings = get_gui_settings()
        self._muted_until = self._load_muted_rules()
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
        self._audit_refresh_btn = QPushButton("Refresh Timeline")
        self._audit_refresh_btn.setObjectName("intelActionBtn")
        self._audit_refresh_btn.clicked.connect(self._refresh_audit_timeline)
        self._summary_bar.addWidget(self._audit_refresh_btn)
        self._unmute_btn = QPushButton("Unmute All")
        self._unmute_btn.setObjectName("intelActionBtn")
        self._unmute_btn.clicked.connect(self._clear_muted_rules)
        self._summary_bar.addWidget(self._unmute_btn)
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

        self._audit_title = QLabel("Security Timeline")
        self._audit_title.setObjectName("intelCategory")
        outer.addWidget(self._audit_title)
        self._audit_list = QListWidget()
        self._audit_list.setObjectName("intelAuditList")
        self._audit_list.setMaximumHeight(120)
        outer.addWidget(self._audit_list)

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
        visible = [f for f in findings if not self._is_muted(f)]

        if not visible:
            self._set_summary("✓ No issues found", Colors.SUCCESS)
            self._set_placeholder("✓ No issues found", Colors.SUCCESS, bold=True)
            with self._suspend_card_updates():
                self._clear_cards()
            self._show_content(show_scroll=False)
            self._refresh_audit_timeline()
            return

        self._set_summary(self._build_summary_html(visible), Colors.FG, rich_text=True)
        self._rebuild_cards(visible)
        self._show_content(show_scroll=True)
        self._refresh_audit_timeline()

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
        self._audit_list.clear()

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

    def _set_summary(self, text: str, color: str, *, rich_text: bool = False) -> None:
        """Update the summary bar label with *text* styled in *color*."""
        self._summary_label.setText(text)
        self._summary_label.setStyleSheet(f"color: {color};")
        self._summary_label.setTextFormat(
            Qt.TextFormat.RichText if rich_text else Qt.TextFormat.PlainText
        )

    def _set_placeholder(self, text: str, color: str = "", *, bold: bool = False) -> None:
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
                parts.append(f'<span style="color:{color_fn()};">{icon} {c} {sev.value}</span>')
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
                self._scroll_layout.insertWidget(self._scroll_layout.count() - 1, cat_label)
                for finding in cat_findings:
                    self._scroll_layout.insertWidget(
                        self._scroll_layout.count() - 1,
                        _FindingCard(
                            finding,
                            on_apply=self._apply_finding_action,
                            on_mute=self._mute_for_seven_days,
                        ),
                    )

    def _apply_finding_action(self, finding: Finding) -> None:
        """Apply finding action to request editor when supported, else copy a fix template."""
        try:
            win = self.window()
            rp = getattr(win, "request_panel", None)
            if rp is None:
                self._copy_finding_template(finding)
                return

            if finding.analyzer_id == "security.missing_headers":
                missing = list((finding.details or {}).get("missing") or [])
                template = _missing_headers_template(missing)
                for line in template.splitlines():
                    if ":" not in line:
                        continue
                    key, header_value = (p.strip() for p in line.split(":", 1))
                    rp.headers_table.add_row(key, header_value, enabled=True)
                return

            if finding.analyzer_id == "recommender":
                ftype = str((finding.details or {}).get("type") or "")
                key = str((finding.details or {}).get("key") or "")
                suggested_value: Any | None = (finding.details or {}).get("suggested_value")
                if ftype == "header" and key:
                    rp.headers_table.add_row(key, str(suggested_value or ""), enabled=True)
                    return
                if ftype == "query" and key:
                    rp.params_table.add_row(key, str(suggested_value or ""), enabled=True)
                    return

            self._copy_finding_template(finding)
        except Exception:
            logger.debug("Failed to apply finding action", exc_info=True)
            self._copy_finding_template(finding)

    @staticmethod
    def _copy_finding_template(finding: Finding) -> None:
        """Copy a textual remediation template for unsupported auto-apply findings."""
        text = finding.recommendation or finding.description
        if finding.analyzer_id == "security.missing_headers":
            text = _missing_headers_template(list((finding.details or {}).get("missing") or []))
        QGuiApplication.clipboard().setText(text)

    def _mute_for_seven_days(self, finding: Finding) -> None:
        key = _finding_key(finding)
        self._muted_until[key] = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
        self._save_muted_rules()
        self.display_findings(self._findings)

    def _is_muted(self, finding: Finding) -> bool:
        key = _finding_key(finding)
        expires = self._muted_until.get(key)
        if not expires:
            return False
        try:
            dt = datetime.fromisoformat(expires)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return datetime.now(timezone.utc) < dt
        except Exception:
            return False

    def _load_muted_rules(self) -> dict[str, str]:
        raw = self._settings.value(_KEY_MUTED_FINDINGS, "{}")
        try:
            data = json.loads(raw) if isinstance(raw, str) else {}
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items()}
        except Exception:
            logger.debug("Failed to parse muted finding rules", exc_info=True)
        return {}

    def _save_muted_rules(self) -> None:
        try:
            self._settings.setValue(_KEY_MUTED_FINDINGS, json.dumps(self._muted_until))
        except Exception:
            logger.debug("Failed to save muted finding rules", exc_info=True)

    def _clear_muted_rules(self) -> None:
        self._muted_until = {}
        self._save_muted_rules()
        self.display_findings(self._findings)

    def _refresh_audit_timeline(self) -> None:
        """Refresh compact audit timeline with recent security-relevant events."""
        self._audit_list.clear()
        audit_path = Path.home() / ".equinox" / "audit.log"
        if not audit_path.exists():
            self._audit_list.addItem(QListWidgetItem("No audit events yet."))
            return

        wanted = {
            "validation_failure",
            "rate_limit_exceeded",
            "ssl_verification_failed",
            "injection_attempt",
            "auth_failure",
            "auth_token_refresh",
        }
        try:
            lines = self._read_recent_audit_lines(audit_path)
            shown = 0
            for line in reversed(lines):
                if shown >= 20:
                    break
                try:
                    event = json.loads(line)
                except Exception:
                    continue
                etype = str(event.get("event_type") or "")
                if etype not in wanted:
                    continue
                ts = str(event.get("timestamp") or "")[:19]
                msg = str(event.get("message") or etype)
                self._audit_list.addItem(QListWidgetItem(f"{ts}  {msg}"))
                shown += 1
            if shown == 0:
                self._audit_list.addItem(QListWidgetItem("No recent security events."))
        except Exception:
            logger.debug("Failed to refresh audit timeline", exc_info=True)
            self._audit_list.addItem(QListWidgetItem("Unable to load audit timeline."))

    @staticmethod
    def _read_recent_audit_lines(path: Path) -> list[str]:
        """Read a bounded tail of the audit log to keep refresh responsive."""
        try:
            with path.open("rb") as fh:
                fh.seek(0, 2)
                size = fh.tell()
                read_from = max(0, size - _AUDIT_TAIL_BYTES)
                fh.seek(read_from)
                data = fh.read()
            text = data.decode("utf-8", errors="replace")
            lines = text.splitlines()
            if len(lines) > _AUDIT_MAX_LINES:
                lines = lines[-_AUDIT_MAX_LINES:]
            return lines
        except Exception:
            logger.debug("Failed to read audit log tail", exc_info=True)
            return []

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
