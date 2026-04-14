"""Shared infrastructure for list+form dialogs with dirty-state tracking.

The three manager dialogs (OAuth Clients, Saved Credentials, Environments)
all share identical patterns for:

- dirty close guard (``_on_close``)
- re-selecting a list item by ID (``_reselect_item``)
- dirty-state prompt before switching (``_prompt_unsaved``)
- status label colouring (``_set_status``)

This mixin extracts those patterns so each dialog only implements the
parts that genuinely differ.  Subclasses set the ``_list_widget`` and
``_save_method`` attributes and everything else works automatically.
"""

from __future__ import annotations

from typing import Callable, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog, QListWidget, QLabel, QMessageBox

from equinox.gui.theme import Colors


class DirtyDialogMixin:
    """Mixin providing common dirty-state logic for list+form dialogs.

    **Required attributes** (set by the subclass before calling helpers):

    ``_dirty``
        ``bool`` — whether the form has unsaved changes.

    ``_list_widget``
        The ``QListWidget`` holding the items.

    ``_save_callback``
        A no-arg callable that persists the current form and returns
        ``True`` on success (e.g. ``self._save_client``).
    """

    _dirty: bool
    _list_widget: QListWidget
    _save_callback: Callable[[], bool]

    # ── Close guard ────────────────────────────────────────────────────

    def _on_close(self) -> None:
        """Prompt to save dirty changes before closing the dialog."""
        if self._dirty:
            ans = QMessageBox.question(
                self,  # type: ignore[arg-type]
                "Unsaved Changes",
                "Save changes before closing?",
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel,
            )
            if ans == QMessageBox.StandardButton.Cancel:
                return
            if ans == QMessageBox.StandardButton.Save:
                if not self._save_callback():
                    return  # save failed — keep dialog open
        self.accept()  # type: ignore[attr-defined]

    # ── Re-select by ID ───────────────────────────────────────────────

    def _reselect_item(self, item_id: int) -> None:
        """Re-select the list item carrying *item_id* without firing signals."""
        self._list_widget.blockSignals(True)
        for i in range(self._list_widget.count()):
            if self._list_widget.item(i).data(Qt.ItemDataRole.UserRole) == item_id:
                self._list_widget.setCurrentRow(i)
                break
        self._list_widget.blockSignals(False)

    # ── Dirty-switch prompt ───────────────────────────────────────────

    def _prompt_unsaved(self, current_id: Optional[int]) -> bool:
        """Ask the user to save, discard, or cancel an item switch.

        Returns ``True`` if the caller should proceed with the switch,
        ``False`` if the switch was cancelled (or save failed).
        """
        ans = QMessageBox.question(
            self,  # type: ignore[arg-type]
            "Unsaved Changes",
            "Save changes to the current item before switching?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
        )
        if ans == QMessageBox.StandardButton.Cancel:
            if current_id is not None:
                self._reselect_item(current_id)
            return False
        if ans == QMessageBox.StandardButton.Save:
            if not self._save_callback():
                if current_id is not None:
                    self._reselect_item(current_id)
                return False
        return True

    # ── Status label helper ───────────────────────────────────────────

    @staticmethod
    def _format_status(msg: str, ok: Optional[bool]) -> str:
        """Return an HTML string for a coloured status message."""
        if ok is True:
            colour = Colors.GREEN
        elif ok is False:
            colour = Colors.RED
        else:
            colour = Colors.FG_MUTED
        return f"<span style='color:{colour};'>{msg}</span>"

