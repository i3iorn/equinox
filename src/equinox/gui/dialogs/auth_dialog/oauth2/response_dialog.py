from __future__ import annotations

import json
from typing import Any

from equinox.gui.theme import get_mono_font
from PyQt6.QtWidgets import QDialog
from PyQt6.QtWidgets import QHBoxLayout
from PyQt6.QtWidgets import QLabel
from PyQt6.QtWidgets import QPushButton
from PyQt6.QtWidgets import QTextEdit
from PyQt6.QtWidgets import QVBoxLayout
from PyQt6.QtWidgets import QWidget


class OAuth2TokenResponseDialog(QDialog):
    """Read‑only dialog for safely displaying a redacted OAuth2 token response."""

    def __init__(
        self,
        data: dict[str, Any],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._response = self._sanitize_response(data)

        self.setWindowTitle("Token Endpoint Response")
        self.resize(560, 420)

        layout = QVBoxLayout(self)

        status_label = QLabel(self._build_status_line())
        status_label.setWordWrap(True)
        layout.addWidget(status_label)

        layout.addWidget(QLabel("Response Headers:"))
        layout.addWidget(self._build_headers_view())

        layout.addWidget(QLabel("Response Body (tokens redacted):"))
        layout.addWidget(self._build_body_view())

        layout.addLayout(self._build_button_row())

    # ------------------------------------------------------------------
    # UI Builders
    # ------------------------------------------------------------------

    def _build_status_line(self) -> str:
        method = self._safe_str(self._response.get("method", "POST"))
        url = self._safe_str(self._response.get("url", ""))
        status = self._safe_str(self._response.get("status_code", "?"))
        return f"{method} {url} → {status}"

    def _build_headers_view(self) -> QTextEdit:
        headers = self._response.get("headers", {})
        view = QTextEdit()
        view.setReadOnly(True)
        view.setFont(get_mono_font())
        view.setPlainText(
            "\n".join(f"{self._safe_str(k)}: {self._safe_str(v)}" for k, v in headers.items()),
        )
        return view

    def _build_body_view(self) -> QTextEdit:
        body = self._response.get("body", {})
        view = QTextEdit()
        view.setReadOnly(True)
        view.setFont(get_mono_font())

        try:
            view.setPlainText(json.dumps(body, indent=2, ensure_ascii=False))
        except Exception:
            view.setPlainText(self._safe_str(body))

        return view

    def _build_button_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addStretch()

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        row.addWidget(close_btn)

        return row

    # ------------------------------------------------------------------
    # Sanitization
    # ------------------------------------------------------------------

    @staticmethod
    def _safe_str(value: Any) -> str:
        """Convert any value to a safe, non‑crashing string."""
        try:
            return str(value)
        except Exception:
            return "<unprintable>"

    def _sanitize_response(self, data: dict[str, Any]) -> dict[str, Any]:
        """Return a sanitized, non‑malicious response dict."""
        sanitized: dict[str, Any] = {}

        for key, value in data.items():
            if key in {"headers", "body"} and isinstance(value, dict):
                sanitized[key] = {self._safe_str(k): self._safe_str(v) for k, v in value.items()}
            else:
                sanitized[key] = self._safe_str(value)

        return sanitized
