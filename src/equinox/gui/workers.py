"""Background worker threads and dialogs for the Equinox GUI."""

import csv
import inspect
import json as _json
import logging
import threading
import time
from datetime import datetime as _dt
from typing import Optional, Callable, Any

from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QPlainTextEdit,
    QDialogButtonBox,
    QFormLayout,
    QFileDialog,
    QMessageBox,
    QProgressBar,
    QSpinBox,
)
from PyQt6.QtCore import QThread, pyqtSignal

from equinox.auth._oauth2 import make_oauth2_basic_auth_header
from equinox.core.client import HTTPClient
from equinox.core.cookies import CookieManager
from equinox.core.validation import Validator
from equinox.security import redact_body
from equinox.core.request import Request, Response
from equinox.core.error_enrichment import RichError, enrich_exception
from equinox.gui.theme import get_mono_font

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30.0

# Percentile thresholds used in benchmark result display and export.
_P95: float = 0.95
_P99: float = 0.99


# ── Private helpers ───────────────────────────────────────────────────────────

def _percentile(sorted_times: list, p: float) -> float:
    """Return the *p*-th percentile value from a pre-sorted sequence of timings."""
    n = len(sorted_times)
    return sorted_times[max(0, int(n * p) - 1)]


def _build_client(
    request: Request,
    cookie_manager: Optional[CookieManager],
    proxy: Optional[str],
    cancel_event: threading.Event,
) -> HTTPClient:
    """Construct an :class:`HTTPClient` from request preferences and run-time context.

    Reads ``timeout``, ``verify_ssl``, and ``follow_redirects`` via ``getattr``
    so the function degrades gracefully when request objects omit these attrs.
    """
    return HTTPClient(
        cookie_manager=cookie_manager,
        timeout=getattr(request, "timeout", DEFAULT_TIMEOUT),
        verify_ssl=getattr(request, "verify_ssl", True),
        follow_redirects=getattr(request, "follow_redirects", True),
        proxy=proxy,
        cancel_event=cancel_event,
    )


class OAuthTokenTester(QThread):
    """Thread that tests OAuth2 token acquisition via a real POST request.

    Emits ``done(success: bool, message: str)`` when finished.
    Call ``cancel()`` before destroying the owner widget to prevent the signal
    from firing into a dead object.
    """

    done = pyqtSignal(bool, str)

    def __init__(
        self,
        token_url: str,
        client_id: str,
        secret: str,
        scope: str,
        grant_type: str,
        extra_params: dict,
        token_auth: str = "body",
        parent=None,
    ):
        super().__init__(parent)
        self.token_url = token_url
        self.client_id = client_id
        self.secret = secret
        self.scope = scope
        self.grant_type = grant_type
        self.extra_params = extra_params
        self.token_auth = token_auth if token_auth in ("body", "basic") else "body"
        self._cancelled = False

    def cancel(self) -> None:
        """Mark this tester as cancelled so no signal fires after the owner closes."""
        self._cancelled = True

    def run(self) -> None:
        try:
            import httpx

            # Validate the token URL before making the request — this enforces
            # SSRF protection and schema checks through the same path as the
            # main HTTP client.
            try:
                Validator.validate_resolved_url(self.token_url)
            except Exception as exc:
                if not self._cancelled:
                    self.done.emit(False, f"Invalid token URL: {exc}")
                return

            data = {
                "grant_type": self.grant_type,
            }
            headers: dict = {}

            if self.token_auth == "basic":
                # RFC 6749 §2.3.1 — credentials in HTTP Basic Authorization header.
                # Reuse the shared utility from OAuth2Auth to avoid duplication.
                try:
                    headers["Authorization"] = make_oauth2_basic_auth_header(
                        self.client_id, self.secret
                    )
                except Exception as exc:
                    if not self._cancelled:
                        self.done.emit(False, str(exc))
                    return
            else:
                # Default: credentials in the POST body
                data["client_id"] = self.client_id
                data["client_secret"] = self.secret

            if self.scope:
                data["scope"] = self.scope
            if self.grant_type == "refresh_token":
                data["refresh_token"] = ""  # placeholder
            data.update(self.extra_params)

            resp = httpx.post(self.token_url, data=data, headers=headers, timeout=10.0)
            if resp.status_code == 200:
                payload = resp.json()
                token_type = payload.get("token_type", "bearer")
                expires_in = payload.get("expires_in")
                has_access = bool(payload.get("access_token"))
                msg = (
                    f"\u2713 Token received  [{token_type}]"
                    + (f"  expires_in={expires_in}s" if expires_in else "")
                    + ("  (no access_token!)" if not has_access else "")
                )
                if not self._cancelled:
                    self.done.emit(True, msg)
            else:
                try:
                    body = resp.json()
                    err = (
                        body.get("error_description")
                        or body.get("error")
                        or resp.text[:200]
                    )
                except Exception:
                    err = resp.text[:200]
                if not self._cancelled:
                    self.done.emit(False, f"HTTP {resp.status_code}: {redact_body(str(err))}")
        except Exception as exc:
            if not self._cancelled:
                self.done.emit(False, redact_body(str(exc)))


# ─────────────────────────────────────────────────────────────────────────────
# Background worker
# ─────────────────────────────────────────────────────────────────────────────

class RequestWorker(QThread):
    """Worker thread for sending HTTP requests.

    Emits ``finished(result)`` where *result* is either a :class:`Response`
    or an :class:`Exception`.  ``cancel()`` marks the result as stale so the
    GUI ignores it even if the TCP connection completes.

    ``proxy`` **must** be resolved on the main thread (from QSettings) and
    injected here — reading QSettings from a background thread is undefined
    behaviour on Windows with the native registry backend.
    """

    finished = pyqtSignal(object)

    def __init__(
        self,
        request: Request,
        parent=None,
        cookie_manager: Optional[CookieManager] = None,
        proxy: Optional[str] = None,
    ):
        super().__init__(parent)
        self.request = request
        self._cancelled = False
        self._cookie_manager = cookie_manager
        self._proxy = proxy
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        self._cancelled = True
        self._cancel_event.set()

    def run(self) -> None:
        try:
            if self._proxy:
                logger.info(
                    "Using proxy: %s (if unexpected, clear proxy settings in Preferences)",
                    self._proxy,
                )
            else:
                logger.debug("No proxy configured")
            client = _build_client(
                self.request, self._cookie_manager, self._proxy, self._cancel_event
            )
            response = client.send(self.request)
            if not self._cancelled:
                self.finished.emit(response)
        except Exception as exc:
            if not self._cancelled:
                self.finished.emit(enrich_exception(exc))


class BackgroundTaskWorker(QThread):
    """Run a Python callable in a worker thread and return its result."""

    finished = pyqtSignal(bool, object)

    def __init__(self, operation: Callable[[], Any], parent=None):
        super().__init__(parent)
        self._operation = operation
        self._cancelled = False
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        """Mark this task as cancelled; result will be ignored."""
        self._cancelled = True
        self._cancel_event.set()
        self.requestInterruption()

    def run(self) -> None:
        if self._cancelled:
            return
        try:
            result = self._invoke_operation()
            if not self._cancelled:
                self.finished.emit(True, result)
        except Exception as exc:
            if not self._cancelled:
                self.finished.emit(False, exc)

    def _invoke_operation(self) -> Any:
        try:
            signature = inspect.signature(self._operation)
        except (TypeError, ValueError):
            return self._operation()

        if "cancel_event" in signature.parameters:
            return self._operation(cancel_event=self._cancel_event)
        if "cancel_token" in signature.parameters:
            return self._operation(cancel_token=self._cancel_event)
        return self._operation()


# ─────────────────────────────────────────────────────────────────────────────
# Benchmark worker thread
# ─────────────────────────────────────────────────────────────────────────────

class BenchmarkWorker(QThread):
    """Run the HTTP request loop off the main thread.

    Signals
    -------
    progress(int)       — emitted after each request with the current iteration count.
    finished(list, int) — emitted when done: (elapsed_times_seconds, error_count).

    Cancellation is handled via a ``threading.Event`` so that an in-flight
    request can be aborted immediately (the event is forwarded to the
    underlying :class:`HTTPClient`).
    """

    progress = pyqtSignal(int)
    finished = pyqtSignal(list, int)

    def __init__(
        self,
        request: Request,
        n: int,
        proxy: Optional[str],
        cookie_manager: Optional[CookieManager],
        parent=None,
    ):
        super().__init__(parent)
        self._request = request
        self._n = n
        self._proxy = proxy
        self._cookie_manager = cookie_manager
        # threading.Event is safe to set from the main thread and read from
        # the worker thread without additional locking.
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        """Request cancellation; aborts the current in-flight request too."""
        self._cancel_event.set()

    def run(self) -> None:
        times: list = []
        errors = 0

        # Create the client once so the underlying connection pool is reused
        # across all iterations — this avoids a full TLS handshake per request
        # and gives accurate latency numbers for keep-alive endpoints.
        # Passing cancel_event allows an in-flight request to be aborted
        # immediately when cancel() is called, not just between iterations.
        client = _build_client(
            self._request, self._cookie_manager, self._proxy, self._cancel_event
        )

        for i in range(self._n):
            if self._cancel_event.is_set():
                break
            try:
                t0 = time.monotonic()
                client.send(self._request)
                times.append(time.monotonic() - t0)
            except Exception:
                errors += 1
                logger.debug(
                    "Benchmark iteration %d/%d failed", i + 1, self._n, exc_info=True
                )
            self.progress.emit(i + 1)

        self.finished.emit(times, errors)


# ─────────────────────────────────────────────────────────────────────────────
# Benchmark dialog
# ─────────────────────────────────────────────────────────────────────────────

class BenchmarkDialog(QDialog):
    """Run the current request N times and display timing statistics."""

    def __init__(self, request: Request, parent=None, cookie_manager: Optional[CookieManager]=None):
        super().__init__(parent)
        self._request = request
        self._cookie_manager = cookie_manager
        self.setWindowTitle("Benchmark")
        self.setMinimumSize(420, 340)
        self._times: list = []
        self._errors: int = 0
        self._stats: dict = {}          # populated by _on_finished; read by _export_results
        self._worker: Optional[BenchmarkWorker] = None
        self._was_cancelled = False  # set by _cancel(); read by _on_finished()
        self._init_ui()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        form = QFormLayout()
        self._count_spin = QSpinBox()
        self._count_spin.setRange(1, 1000)
        self._count_spin.setValue(10)
        form.addRow("Number of requests:", self._count_spin)
        layout.addLayout(form)

        btn_row = QHBoxLayout()
        self._run_btn = QPushButton("Run Benchmark")
        self._run_btn.clicked.connect(self._run)
        btn_row.addWidget(self._run_btn)

        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.setEnabled(False)
        self._cancel_btn.clicked.connect(self._cancel)
        btn_row.addWidget(self._cancel_btn)
        layout.addLayout(btn_row)

        self._progress = QProgressBar()
        self._progress.setVisible(False)
        layout.addWidget(self._progress)

        self._results = QPlainTextEdit()
        self._results.setReadOnly(True)
        self._results.setFont(get_mono_font())
        self._results.setPlaceholderText("Results will appear here after running.")
        layout.addWidget(self._results, 1)

        bottom_row = QHBoxLayout()
        self._export_btn = QPushButton("Export\u2026")
        self._export_btn.setEnabled(False)
        self._export_btn.setToolTip("Export benchmark results to CSV or JSON")
        self._export_btn.clicked.connect(self._export_results)
        close_btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_btns.rejected.connect(self.reject)
        bottom_row.addWidget(self._export_btn)
        bottom_row.addStretch()
        bottom_row.addWidget(close_btns)
        layout.addLayout(bottom_row)

    # ── Private helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _compute_stats(times: list, errors: int) -> dict:
        """Return a stats dict computed from raw benchmark timings.

        All ``*_ms`` values are rounded to 3 decimal places.
        ``times_ms`` preserves the original iteration order for per-row CSV export.

        Raises:
            ValueError: If *times* is empty (no successful measurements).
        """
        if not times:
            raise ValueError("_compute_stats requires at least one successful timing entry")
        times_s = sorted(times)
        n_ok = len(times_s)
        avg = sum(times_s) / n_ok
        return {
            "n_ok":     n_ok,
            "errors":   errors,
            "min_ms":   round(times_s[0] * 1000, 3),
            "avg_ms":   round(avg * 1000, 3),
            "max_ms":   round(times_s[-1] * 1000, 3),
            "p95_ms":   round(_percentile(times_s, _P95) * 1000, 3),
            "p99_ms":   round(_percentile(times_s, _P99) * 1000, 3),
            # Per-iteration timings in original run order for CSV export:
            "times_ms": [round(t * 1000, 3) for t in times],
        }

    def _run(self) -> None:
        from equinox.gui.ui_common import get_gui_settings, resolve_proxy_url

        n = self._count_spin.value()
        self._progress.setMaximum(n)
        self._progress.setValue(0)
        self._progress.setVisible(True)
        self._run_btn.setEnabled(False)
        self._cancel_btn.setEnabled(True)
        self._export_btn.setEnabled(False)
        self._results.setPlainText("Running\u2026")
        self._was_cancelled = False

        # Resolve proxy on the main thread (safe for QSettings on all platforms).
        proxy = resolve_proxy_url(settings=get_gui_settings(), logger=logger)

        self._worker = BenchmarkWorker(
            request=self._request,
            n=n,
            proxy=proxy,
            cookie_manager=self._cookie_manager,
            parent=self,
        )
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.start()

    def _cancel(self) -> None:
        if self._worker and self._worker.isRunning():
            self._was_cancelled = True
            self._worker.cancel()
            self._cancel_btn.setEnabled(False)
            self._results.setPlainText("Cancelling\u2026")

    def _on_progress(self, value: int) -> None:
        self._progress.setValue(value)

    def _on_finished(self, times: list, errors: int) -> None:
        self._run_btn.setEnabled(True)
        self._cancel_btn.setEnabled(False)
        self._progress.setVisible(False)

        # Release the worker and let Qt clean up the C++ thread object.
        worker, self._worker = self._worker, None
        if worker is not None:
            worker.deleteLater()

        n = self._progress.maximum()
        n_done = len(times) + errors

        if self._was_cancelled:
            self._was_cancelled = False
            msg = f"Cancelled after {n_done} of {n} request(s)."
            if times:
                # Partial results are still useful — show them and allow export.
                self._times = times
                self._errors = errors
                self._stats = self._compute_stats(times, errors)
                self._results.setPlainText(msg + "\n\nPartial results below:\n")
                self._export_btn.setEnabled(True)
            else:
                self._results.setPlainText(msg)
            return

        if not times:
            self._results.setPlainText(f"All {n} request(s) failed ({errors} errors).")
            return

        self._times = times
        self._errors = errors
        self._stats = self._compute_stats(times, errors)
        s = self._stats

        self._results.setPlainText(
            f"Requests : {n}\n"
            f"Success  : {s['n_ok']}\n"
            f"Errors   : {s['errors']}\n"
            f"\n"
            f"Min      : {s['min_ms']:.1f} ms\n"
            f"Avg      : {s['avg_ms']:.1f} ms\n"
            f"Max      : {s['max_ms']:.1f} ms\n"
            f"p95      : {s['p95_ms']:.1f} ms\n"
            f"p99      : {s['p99_ms']:.1f} ms\n"
        )
        self._export_btn.setEnabled(True)

    def reject(self) -> None:
        """Disconnect signals and wait for the worker before closing.

        Without this, a running worker thread can emit ``finished`` into the
        already-destroyed dialog widgets and cause a segfault.
        """
        if self._worker is not None:
            # Disconnect first so no callbacks fire into the closing dialog.
            try:
                self._worker.progress.disconnect()
                self._worker.finished.disconnect()
            except RuntimeError:
                pass  # signals were already disconnected
            self._worker.cancel()
            if not self._worker.wait(1000):
                # Didn't finish in time — log and move on; don't block the UI.
                logger.debug("BenchmarkWorker did not stop within 1 s on dialog close")
            self._worker = None
        super().reject()

    def _export_results(self) -> None:
        """Export benchmark timing data to CSV or JSON chosen by the user."""
        if not self._times:
            return

        s = self._stats
        summary = {
            "url":        self._request.url,
            "method":     self._request.method,
            "run_at":     _dt.now().isoformat(timespec="seconds"),
            "requests":   s["n_ok"] + s["errors"],
            "success":    s["n_ok"],
            "errors":     s["errors"],
            "min_ms":     s["min_ms"],
            "avg_ms":     s["avg_ms"],
            "max_ms":     s["max_ms"],
            "p95_ms":     s["p95_ms"],
            "p99_ms":     s["p99_ms"],
            "iterations": s["times_ms"],
        }

        path, selected_filter = QFileDialog.getSaveFileName(
            self, "Export Benchmark Results", "benchmark.json",
            "JSON files (*.json);;CSV files (*.csv)",
        )
        if not path:
            return

        try:
            if selected_filter.startswith("CSV") or path.lower().endswith(".csv"):
                with open(path, "w", newline="", encoding="utf-8") as f:
                    writer = csv.writer(f)
                    writer.writerow(["iteration", "elapsed_ms"])
                    for idx, ms in enumerate(s["times_ms"], 1):
                        writer.writerow([idx, ms])
            else:
                with open(path, "w", encoding="utf-8") as f:
                    _json.dump(summary, f, indent=2)
        except Exception as exc:
            QMessageBox.warning(self, "Export Failed", f"Could not write file: {exc}")
