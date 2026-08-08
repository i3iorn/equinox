from __future__ import annotations

from PyQt6.QtWidgets import QDialog
from PyQt6.QtWidgets import QInputDialog
from PyQt6.QtWidgets import QMessageBox
from PyQt6.QtWidgets import QWidget

from ..ui_common import confirm_yes_no
from .variable_dialog import VariableDialog


class GroupDialogs:
    """Encapsulates all dialogs and user prompts for groups/variables."""

    def __init__(self, parent: QWidget) -> None:
        self.parent = parent

    # ÔöÇÔöÇ Group dialogs ÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇ

    def ask_group_name(self) -> tuple[str | None, bool]:
        name, ok = QInputDialog.getText(self.parent, "New Variable Group", "Group name:")
        accepted = bool(ok)
        return (name if accepted and name else None, accepted)

    def ask_group_description(self) -> tuple[str | None, bool]:
        desc, ok = QInputDialog.getText(
            self.parent,
            "New Variable Group",
            "Description (optional):",
        )
        accepted = bool(ok)
        return (desc if accepted else None, accepted)

    def confirm_delete_group(self, name: str) -> bool:
        return bool(
            confirm_yes_no(
                self.parent,
                "Confirm Delete",
                f"Delete variable group '{name}' and all its variables?",
            ),
        )

    def ask_rename_group(self, old_name: str) -> tuple[str | None, bool]:
        new_name, ok = QInputDialog.getText(
            self.parent,
            "Rename Group",
            "New name:",
            text=old_name,
        )
        accepted = bool(ok)
        return (new_name if accepted and new_name else None, accepted)

    # ÔöÇÔöÇ Variable dialogs ÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇ

    def edit_variable(
        self,
        key: str,
        value: str,
        desc: str,
    ) -> tuple[str, str, str] | None:
        dialog = VariableDialog(self.parent, key, value, desc)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        values = dialog.get_values()
        return (str(values[0]), str(values[1]), str(values[2]))

    def new_variable(self) -> tuple[str, str, str] | None:
        dialog = VariableDialog(self.parent)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        values = dialog.get_values()
        return (str(values[0]), str(values[1]), str(values[2]))

    # ÔöÇÔöÇ Messages ÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇ

    def show_success(self, message: str) -> None:
        QMessageBox.information(self.parent, "Success", message)

    def show_error(self, message: str) -> None:
        QMessageBox.critical(self.parent, "Error", message)
