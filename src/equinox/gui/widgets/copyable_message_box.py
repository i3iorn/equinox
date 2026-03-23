"""QMessageBox subclass with a built-in *Copy* button."""

from typing import Optional

from PyQt6.QtWidgets import QApplication, QMessageBox, QPushButton, QWidget


class CopyableMessageBox(QMessageBox):
    """A ``QMessageBox`` that always includes **OK** and **Copy** buttons.

    *copy_text* is the string placed on the clipboard when the user clicks
    **Copy**.  If omitted it defaults to the *text* (main message body).

    Usage::

        CopyableMessageBox.critical(parent, "Title", "Visible message",
                                     copy_text=full_traceback)
    """

    def __init__(
        self,
        icon: QMessageBox.Icon,
        title: str,
        text: str,
        copy_text: Optional[str] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(icon, title, text, QMessageBox.StandardButton.Ok, parent)
        self._copy_text = copy_text or text
        self._copy_btn: QPushButton = self.addButton(
            "Copy", QMessageBox.ButtonRole.ActionRole
        )

    def exec(self) -> int:
        """Show the dialog; re-open if the user clicks *Copy*."""
        while True:
            result = super().exec()
            if self.clickedButton() == self._copy_btn:
                QApplication.clipboard().setText(self._copy_text)
                continue          # keep the dialog open
            return result

    # ── Convenience class methods (mirror QMessageBox API) ────────────

    @classmethod
    def critical(
        cls,
        parent: Optional[QWidget],
        title: str,
        text: str,
        copy_text: Optional[str] = None,
    ) -> int:
        """Show a *Critical* dialog with OK + Copy buttons."""
        return cls(QMessageBox.Icon.Critical, title, text, copy_text, parent).exec()

    @classmethod
    def warning(
        cls,
        parent: Optional[QWidget],
        title: str,
        text: str,
        copy_text: Optional[str] = None,
    ) -> int:
        """Show a *Warning* dialog with OK + Copy buttons."""
        return cls(QMessageBox.Icon.Warning, title, text, copy_text, parent).exec()

    @classmethod
    def information(
        cls,
        parent: Optional[QWidget],
        title: str,
        text: str,
        copy_text: Optional[str] = None,
    ) -> int:
        """Show an *Information* dialog with OK + Copy buttons."""
        return cls(QMessageBox.Icon.Information, title, text, copy_text, parent).exec()

