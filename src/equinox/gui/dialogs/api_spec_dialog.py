"""Dialog to display API spec text in multiple formats."""

import json
import logging
import os
import re
import tempfile

from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)

# Size limits (bytes)
_PRETTY_PRINT_MAX = 1_000_000  # 1 MB — above this, show truncated raw text
_CLIPBOARD_MAX = 1_000_000  # 1 MB — warn before copying larger content
_SAVE_MAX = 10_000_000  # 10 MB — warn before saving larger content

# Keywords used for heuristic secret detection in clipboard copy
_SECRET_KEYWORDS = frozenset(
    {"client_secret", "client-secret", "secret", "token", "authorization", "api_key", "api-key"},
)


def _sanitize_filename(name: str, max_len: int = 200) -> str:
    """Return *name* with characters that are unsafe in filenames replaced by ``_``.

    Characters replaced: ``<>:"/\\|?*`` and ASCII control characters (0–31).
    The result is clamped to *max_len* characters.
    """
    if not name:
        return "spec"
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    return cleaned[:max_len]


def _atomic_write(path: str, content: str) -> None:
    """Write *content* to *path* atomically using a temp file + ``os.replace``.

    The temp file is created in the same directory as *path* so that
    ``os.replace`` is guaranteed to be atomic on POSIX (same filesystem).
    Raises ``OSError`` on failure; the temp file is always cleaned up.
    """
    directory = os.path.dirname(os.path.abspath(path))
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            delete=False,
            dir=directory,
        ) as tmp:
            tmp_path = tmp.name
            tmp.write(content)
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(tmp_path, path)
        tmp_path = None  # successfully renamed; nothing to clean up
    finally:
        if tmp_path:
            try:
                os.remove(tmp_path)
            except OSError:
                logger.debug("Failed to remove temp file %s", tmp_path, exc_info=True)


class ApiSpecDialog(QDialog):
    """Display API spec text in multiple formats with copy/save actions.

    Usage::

        dlg = ApiSpecDialog(parent, title)
        dlg.set_variants({'OpenAPI': openapi_str, 'Postman': postman_str})
        dlg.exec()
    """

    def __init__(self, parent: QWidget | None = None, title: str = "API Spec") -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(700, 480)

        self._variants: dict[str, str] = {}
        self._allow_clipboard = True

        lay = QVBoxLayout(self)

        top = QHBoxLayout()
        top.addWidget(QLabel("Format:"))
        self.format_combo = QComboBox()
        self.format_combo.currentIndexChanged.connect(self._update_preview_for_current)
        top.addWidget(self.format_combo)
        top.addStretch()
        lay.addLayout(top)

        self.preview = QTextEdit()
        self.preview.setReadOnly(True)
        mono = QFont("Courier New")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self.preview.setFont(mono)
        self.preview.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        lay.addWidget(self.preview, 1)

        btn_row = QHBoxLayout()
        self.copy_btn = QPushButton("Copy")
        self.copy_btn.clicked.connect(self._on_copy)
        self.save_btn = QPushButton("Save…")
        self.save_btn.clicked.connect(self._on_save)
        self.close_btn = QPushButton("Close")
        self.close_btn.clicked.connect(self.accept)
        btn_row.addStretch()
        btn_row.addWidget(self.copy_btn)
        btn_row.addWidget(self.save_btn)
        btn_row.addWidget(self.close_btn)
        lay.addLayout(btn_row)

    # ── Public API ────────────────────────────────────────────────────────

    def set_variants(self, variants: dict[str, str]) -> None:
        """Provide a mapping of display-name → text.

        The combo is populated with the keys in dict insertion order.
        """
        self._variants = variants or {}
        self.format_combo.blockSignals(True)
        try:
            self.format_combo.clear()
            for name in self._variants:
                self.format_combo.addItem(name)
        finally:
            self.format_combo.blockSignals(False)
        if self.format_combo.count() > 0:
            self.format_combo.setCurrentIndex(0)
            self._update_preview_for_current()
        else:
            self.preview.setPlainText("")

    def set_allow_clipboard(self, allow: bool) -> None:
        """Enable or disable clipboard copying from this dialog (default: True).

        Callers can disable this to avoid accidental exfiltration of secrets.
        """
        self._allow_clipboard = bool(allow)

    # ── Private helpers ───────────────────────────────────────────────────

    def _update_preview_for_current(self) -> None:
        name = self.format_combo.currentText()
        text = self._variants.get(name, "")
        if not text:
            self.preview.setPlainText("")
            return

        size = len(text.encode("utf-8"))
        if size > _PRETTY_PRINT_MAX:
            notice = f"\n\n[Preview truncated: {size:,} bytes > {_PRETTY_PRINT_MAX:,} byte limit]"
            self.preview.setPlainText(text[:4096] + notice)
            return

        try:
            pretty = json.dumps(json.loads(text), indent=2, ensure_ascii=False)
            self.preview.setPlainText(pretty)
        except json.JSONDecodeError:
            self.preview.setPlainText(text)
        except Exception:
            logger.exception("Unexpected error while rendering spec preview")
            self.preview.setPlainText(text)

    def _on_copy(self) -> None:
        text = self.preview.toPlainText()
        if not text:
            return

        if not self._allow_clipboard:
            QMessageBox.warning(
                self,
                "Copy blocked",
                "Copying to clipboard is disabled for this dialog.",
            )
            logger.info("Clipboard copy blocked by policy")
            return

        # Heuristic secret detection
        lowered = text.lower()
        if any(k in lowered for k in _SECRET_KEYWORDS):
            reply = QMessageBox.question(
                self,
                "Possible secrets detected",
                "The content may contain secrets (tokens or keys). "
                "Copying to the system clipboard could expose them to other applications. Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                logger.info("User declined copying content that may contain secrets")
                return

        size = len(text.encode("utf-8"))
        if size > _CLIPBOARD_MAX:
            reply = QMessageBox.question(
                self,
                "Large content",
                f"The content is {size:,} bytes. Copying may be slow. Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                logger.info("User cancelled large clipboard copy (size=%d)", size)
                return

        clipboard = QApplication.clipboard()
        if clipboard is None:
            logger.warning("Clipboard is unavailable; skipping copy")
            return
        clipboard.setText(text)
        logger.info("Copied spec to clipboard (size=%d)", size)

    def _on_save(self) -> None:
        text = self.preview.toPlainText()
        if not text:
            return

        suggested = _sanitize_filename(self.windowTitle().replace(" ", "_")) + ".json"
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Spec",
            suggested,
            "JSON Files (*.json);;All Files (*)",
        )
        if not path:
            return

        # Ensure .json extension when user omitted it
        if not os.path.splitext(path)[1]:
            path += ".json"

        # Guard against null bytes in paths (shouldn't happen via QFileDialog
        # on supported platforms, but reject proactively).
        if "\x00" in path:
            logger.warning("Rejecting save path containing null byte")
            QMessageBox.critical(self, "Save failed", "The selected file path is invalid.")
            return

        size = len(text.encode("utf-8"))
        if size > _SAVE_MAX:
            reply = QMessageBox.question(
                self,
                "Large file",
                f"The file is {size:,} bytes and may take time to save. Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                logger.info("User cancelled saving large spec (size=%d)", size)
                return

        # Confirm overwrite when QFileDialog did not already handle it (Linux).
        if os.path.exists(path):
            reply = QMessageBox.question(
                self,
                "Overwrite file?",
                f"'{os.path.basename(path)}' already exists. Overwrite?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        try:
            _atomic_write(path, text)
            logger.info("Spec saved to %s (size=%d)", path, size)
        except OSError as exc:
            logger.exception("Failed to save spec to %s", path)
            QMessageBox.critical(self, "Save failed", f"Failed to save file: {exc}")
        except Exception:
            logger.exception("Unexpected error saving spec to %s", path)
            QMessageBox.critical(self, "Save failed", "An unexpected error occurred while saving.")
