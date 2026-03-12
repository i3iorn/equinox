from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QTextEdit,
    QPushButton, QFileDialog, QApplication, QMessageBox
)
# Qt not required directly in this dialog
from PyQt6.QtGui import QFont
import json
import os
import re
import tempfile
import logging


class ApiSpecDialog(QDialog):
    """Dialog to display API spec text in multiple formats.

    Usage:
        dlg = ApiSpecDialog(parent, title)
        dlg.set_variants({'OpenAPI': openapi_str, 'Postman': postman_str})
        dlg.exec()
    """

    def __init__(self, parent=None, title: str = "API Spec"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(700, 480)

        # Security / UX controls
        self._variants = {}
        self._allow_clipboard = True

        lay = QVBoxLayout(self)

        top = QHBoxLayout()
        top.addWidget(QLabel("Format:"))
        self.format_combo = QComboBox()
        self.format_combo.currentIndexChanged.connect(self._on_format_changed)
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

    def set_variants(self, variants: dict):
        """Provide a mapping of display-name -> text to show.

        The combo is populated with the keys in the order of the dict.
        """
        self._variants = variants or {}
        self.format_combo.blockSignals(True)
        try:
            self.format_combo.clear()
            for name in self._variants.keys():
                self.format_combo.addItem(name)
        finally:
            self.format_combo.blockSignals(False)
        # Select the first
        if self.format_combo.count() > 0:
            self.format_combo.setCurrentIndex(0)
            self._update_preview_for_current()
        else:
            self.preview.setPlainText("")

    def set_allow_clipboard(self, allow: bool) -> None:
        """Enable or disable clipboard copying from this dialog.

        Default: True. Tests and callers can disable clipboard to avoid
        accidental exfiltration of secrets.
        """
        self._allow_clipboard = bool(allow)

    def _on_format_changed(self, idx: int):
        self._update_preview_for_current()

    def _update_preview_for_current(self):
        name = self.format_combo.currentText()
        text = self._variants.get(name, "")
        # Pretty-print JSON when possible
        # Limit for pretty-printing to avoid UI/CPU DoS from huge specs
        PRETTY_PRINT_MAX_BYTES = 1_000_000  # 1 MB
        try:
            if not text:
                self.preview.setPlainText("")
                return
            size = len(text.encode('utf-8'))
            if size > PRETTY_PRINT_MAX_BYTES:
                # Show a truncated preview with a clear notice
                head = text[:4096]
                notice = f"\n\n[Preview truncated: size {size} bytes > {PRETTY_PRINT_MAX_BYTES} bytes]"
                self.preview.setPlainText(head + notice)
                return
            # Attempt JSON parse and pretty-print; only catch decode errors
            obj = json.loads(text)
            pretty = json.dumps(obj, indent=2, ensure_ascii=False)
            self.preview.setPlainText(pretty)
        except json.JSONDecodeError:
            # Not JSON — just show raw text
            self.preview.setPlainText(text or "")
        except Exception:
            # Unexpected error — log and fall back to raw text
            logging.getLogger(__name__).exception("Unexpected error while updating spec preview")
            self.preview.setPlainText(text or "")

    def _on_copy(self):
        text = self.preview.toPlainText()
        if not text:
            return

        logger = logging.getLogger(__name__)

        # Clipboard policy
        CLIPBOARD_MAX_BYTES = 1_000_000  # 1 MB
        size = len(text.encode('utf-8'))
        if not self._allow_clipboard:
            QMessageBox.warning(self, "Copy blocked", "Copying to clipboard is disabled for this dialog.")
            logger.info("Clipboard copy blocked by policy")
            return

        # Heuristic secret detection
        lowered = text.lower()
        if any(k in lowered for k in ("client_secret", "client-secret", "secret", "token", "authorization", "api_key", "api-key")):
            ok = QMessageBox.question(
                self,
                "Copy contains possible secrets",
                "The content appears to contain secrets (tokens or client secrets). Copying to the system clipboard may expose them to other applications. Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if ok != QMessageBox.StandardButton.Yes:
                logger.info("User declined copying content that may contain secrets")
                return

        if size > CLIPBOARD_MAX_BYTES:
            ok = QMessageBox.question(
                self,
                "Large content",
                f"The content is large ({size} bytes). Copying to the clipboard may be slow. Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if ok != QMessageBox.StandardButton.Yes:
                logger.info("User cancelled large clipboard copy")
                return

        QApplication.clipboard().setText(text)
        logger.info("Copied spec to clipboard (size=%d)", size)

    def _on_save(self):
        text = self.preview.toPlainText()
        if not text:
            return
        logger = logging.getLogger(__name__)

        # Helpers / limits
        PRETTY_PRINT_MAX_BYTES = 1_000_000
        SAVE_MAX_BYTES = 10_000_000  # 10 MB

        def sanitize_filename(name: str, max_len: int = 200) -> str:
            # Remove path separators and control/unsafe chars
            if not name:
                return "spec"
            # Replace invalid chars with underscore
            # Keep a conservative whitelist/blacklist: replace characters that are
            # problematic in filenames across platforms (<>:"/\|?* and control chars)
            cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
            # Also remove os-specific path separators just in case
            cleaned = os.path.basename(cleaned)
            if len(cleaned) > max_len:
                cleaned = cleaned[:max_len]
            return cleaned

        def safe_atomic_write(path: str, content: str) -> None:
            parent = os.path.dirname(path) or os.getcwd()
            fd = None
            tmp_path = None
            try:
                # Create temp file in destination directory to ensure os.replace is atomic on same filesystem
                with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', delete=False, dir=parent) as tmp:
                    tmp_path = tmp.name
                    tmp.write(content)
                    tmp.flush()
                    os.fsync(tmp.fileno())
                # Atomic replace
                os.replace(tmp_path, path)
                tmp_path = None
            finally:
                if tmp_path and os.path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except Exception:
                        logger = logging.getLogger(__name__)
                        logger.exception("Failed to remove temp file %s", tmp_path)

        suggested = sanitize_filename(self.windowTitle().replace(' ', '_')) + ".json"
        path, _ = QFileDialog.getSaveFileName(self, "Save Spec", suggested, "JSON Files (*.json);;All Files (*)")
        if not path:
            return

        # Ensure .json extension if user omitted
        base, ext = os.path.splitext(path)
        if not ext:
            path = base + ".json"

        # Never allow user-supplied title to influence path traversal; QFileDialog returns an absolute path
        # but sanitize filename usage earlier prevents accidental relative suggestions. Still, enforce basename usage
        # when the user typed a path-like title into the dialog's name field on some platforms.
        # We will respect the directory the user chose (from QFileDialog) and only adjust the filename if it
        # appears to contain path separators. If path contains \0 or similar, bail out.
        try:
            if '\x00' in path:
                raise ValueError("Invalid null byte in path")
        except Exception:
            logger.exception("Invalid path chosen for save: %s", path)
            QMessageBox.critical(self, "Save failed", "The selected file path is invalid.")
            return

        size = len(text.encode('utf-8'))
        if size > SAVE_MAX_BYTES:
            # Prompt the user to confirm saving very large content
            ok = QMessageBox.question(
                self,
                "Large file",
                f"The file is large ({size} bytes) and may take time to save. Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if ok != QMessageBox.StandardButton.Yes:
                logger.info("User cancelled saving large spec (size=%d)", size)
                return

        # If target exists, confirm overwrite
        try:
            if os.path.exists(path):
                ok = QMessageBox.question(
                    self,
                    "Overwrite file?",
                    f"The file '{os.path.basename(path)}' already exists. Overwrite?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if ok != QMessageBox.StandardButton.Yes:
                    logger.info("User declined to overwrite %s", path)
                    return

            try:
                safe_atomic_write(path, text)
            except (OSError, IOError) as e:
                logger.exception("Failed to save spec to %s", path)
                QMessageBox.critical(self, "Save failed", f"Failed to save spec: {e}")
            except Exception:
                logger.exception("Unexpected error during save to %s", path)
                QMessageBox.critical(self, "Save failed", "An unexpected error occurred while saving the file.")
        finally:
            # No-op: temp cleanup handled in helper
            pass

