"""Tests for the non-blocking toast notification widget and manager."""

from __future__ import annotations

from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QVBoxLayout, QWidget

from equinox.gui.widgets.toast import ToastManager
from equinox.gui.widgets.toast import ToastWidget


class TestToastWidget:
    """ToastWidget renders a message, kind, and can be dismissed."""

    def test_constructs_with_message_and_kind(self):
        toast = ToastWidget("Saved to collection", kind="success")
        assert toast._message_label.text() == "Saved to collection"
        assert toast.property("toastKind") == "success"

    def test_dismiss_marks_closed(self):
        toast = ToastWidget("Temporary", kind="info")
        toast.dismiss()
        # dismiss() is asynchronous (animation); the timer is stopped.
        assert toast._timer.isActive() is False


class TestToastManager:
    """ToastManager stacks toasts in a container layout."""

    def setup_method(self):
        self._container = QWidget()
        self._layout = QVBoxLayout(self._container)
        self._manager = ToastManager(self._container, self._layout)

    def test_show_adds_widget_to_layout(self):
        toast = self._manager.show("Hello", kind="info")
        assert self._layout.count() == 1
        assert self._layout.itemAt(0).widget() is toast

    def test_success_warning_error_info_set_kind(self):
        for kind, method in (
            ("success", "success"),
            ("warning", "warning"),
            ("error", "error"),
            ("info", "info"),
        ):
            toast = getattr(self._manager, method)(f"{kind} message")
            assert toast.property("toastKind") == kind

    def test_clear_removes_all_toasts(self):
        self._manager.show("one")
        self._manager.show("two")
        assert self._layout.count() == 2
        self._manager.clear()
        # Toast dismissal is animated (220 ms); wait for deleteLater/destroyed
        # so _drop() runs and removes toasts from the manager/layout.
        QTest.qWait(350)
        assert self._manager._toasts == []
        assert self._layout.count() == 0
