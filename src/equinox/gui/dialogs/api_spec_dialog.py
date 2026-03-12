from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QTextEdit,
    QPushButton, QFileDialog, QApplication
)
# Qt not required directly in this dialog
from PyQt6.QtGui import QFont
import json


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

        self._variants = {}

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

    def _on_format_changed(self, idx: int):
        self._update_preview_for_current()

    def _update_preview_for_current(self):
        name = self.format_combo.currentText()
        text = self._variants.get(name, "")
        # Pretty-print JSON when possible
        try:
            obj = json.loads(text)
            pretty = json.dumps(obj, indent=2, ensure_ascii=False)
            self.preview.setPlainText(pretty)
        except Exception:
            self.preview.setPlainText(text or "")

    def _on_copy(self):
        QApplication.clipboard().setText(self.preview.toPlainText())

    def _on_save(self):
        text = self.preview.toPlainText()
        if not text:
            return
        suggested = self.windowTitle().replace(" ", "_") + ".json"
        path, _ = QFileDialog.getSaveFileName(self, "Save Spec", suggested, "JSON Files (*.json);;All Files (*)")
        if path:
            try:
                with open(path, 'w', encoding='utf-8') as f:
                    f.write(text)
            except Exception as e:
                # Keep dialog simple — fail silently (could show QMessageBox)
                print(f"Failed to save spec: {e}")

