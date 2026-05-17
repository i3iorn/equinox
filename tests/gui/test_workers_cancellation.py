"""Targeted cancellation and cooperative-stop tests for GUI workers."""

from __future__ import annotations

import threading

from equinox.gui.workers import BackgroundTaskWorker


def test_background_task_worker_injects_cancel_event_parameter() -> None:
    captured = {"is_event": False}

    def operation(cancel_event):
        captured["is_event"] = isinstance(cancel_event, threading.Event)
        return "ok"

    worker = BackgroundTaskWorker(operation)
    assert worker._invoke_operation() == "ok"
    assert captured["is_event"] is True


def test_background_task_worker_injects_cancel_token_parameter() -> None:
    captured = {"is_event": False}

    def operation(cancel_token):
        captured["is_event"] = isinstance(cancel_token, threading.Event)
        return "ok"

    worker = BackgroundTaskWorker(operation)
    assert worker._invoke_operation() == "ok"
    assert captured["is_event"] is True


def test_background_task_worker_cancel_prevents_finished_signal_emission() -> None:
    emitted = []

    def operation() -> str:
        return "done"

    worker = BackgroundTaskWorker(operation)
    worker.finished.connect(lambda ok, payload: emitted.append((ok, payload)))

    worker.cancel()
    worker.run()

    assert emitted == []


def test_background_task_worker_operation_observes_set_cancel_event() -> None:
    observed = {"cancelled": False}

    def operation(cancel_event) -> str:
        observed["cancelled"] = cancel_event.is_set()
        return "cancelled" if cancel_event.is_set() else "running"

    worker = BackgroundTaskWorker(operation)
    worker._cancel_event.set()

    assert worker._invoke_operation() == "cancelled"
    assert observed["cancelled"] is True

