"""Dialog lifecycle registry for non-modal collection-panel dialogs."""

from __future__ import annotations

from typing import List

from PyQt6.QtWidgets import QDialog


class DialogRegistry:
    """Track open dialogs to prevent early garbage collection."""

    def __init__(self) -> None:
        self._dialogs: List[QDialog] = []

    def register(self, dialog: QDialog) -> None:
        self._dialogs.append(dialog)
        dialog.finished.connect(lambda _code, d=dialog: self._discard(d))
        dialog.destroyed.connect(lambda _obj=None, d=dialog: self._discard(d))

    def _discard(self, dialog: QDialog) -> None:
        if dialog in self._dialogs:
            self._dialogs.remove(dialog)

