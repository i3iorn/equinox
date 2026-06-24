from unittest.mock import patch

from equinox.gui.widgets.copyable_message_box import CopyableMessageBox
from PyQt6.QtWidgets import QApplication
from PyQt6.QtWidgets import QMessageBox


class _FailingClipboard:
    def setText(self, text: str) -> None:
        raise RuntimeError("clipboard unavailable")


def test_done_ignores_clipboard_write_failures() -> None:
    box = CopyableMessageBox(
        QMessageBox.Icon.Warning,
        "Title",
        "Visible message",
        copy_text="copy me",
    )

    with (
        patch.object(box, "clickedButton", return_value=box._copy_btn),
        patch.object(
            QApplication,
            "clipboard",
            return_value=_FailingClipboard(),
        ),
    ):
        box.done(0)

    assert box._copy_btn.text() == box._COPY_LABEL
