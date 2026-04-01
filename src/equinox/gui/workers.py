"""Background worker threads and dialogs for the Equinox GUI."""

import logging
import threading
from typing import Optional

# Percentile thresholds used in benchmark result display and export.
_P95 = 0.95
_P99 = 0.99

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
)
from PyQt6.QtCore import QThread, pyqtSignal

from equinox.core.client import HTTPClient
from equinox.core.cookies import CookieManager
from equinox.core.redact import redact_body
from equinox.core.request import Request, Response
from equinox.core.error_enrichment import RichError, enrich_exception
from equinox.gui.theme import get_mono_font

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30.0


class OAuthTokenTester(QThread):
    """Thread that tests OAuth2 token acquisition via a real POST request.

    Emits ``done(success: bool, message: str)`` when finished.
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
        parent=None,
    ):
        super().__init__(parent)
        self.token_url = token_url
        self.client_id = client_id
        self.secret = secret
        self.scope = scope
        self.grant_type = grant_type
        self.extra_params = extra_params

    def run(self) -> None:
        try:
            import httpx

            data = {
                "grant_type": self.grant_type,
                "client_id": self.client_id,
                "client_secret": self.secret,
            }
            if self.scope:
                data["scope"] = self.scope
            if self.grant_type == "refresh_token":
                data["refresh_token"] = ""  # placeholder
            data.update(self.extra_params)

            resp = httpx.post(self.token_url, data=data, timeout=10.0)
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
                self.done.emit(False, f"HTTP {resp.status_code}: {redact_body(str(err))}")
        except Exception as exc:
            self.done.emit(False, redact_body(str(exc)))


# ─────────────────────────────────────────────────────────────────────────────
# Background worker
# ─────────────────────────────────────────────────────────────────────────────

class RequestWorker(QThread):
    """Worker thread for sending HTTP requests.

    Emits ``finished(result)`` where *result* is either a :class:`Response`
    or an :class:`Exception`.  ``cancel()`` marks the result as stale so the
    GUI ignores it even if the TCP connection completes.
    """

    finished = pyqtSignal(object)

    def __init__(self, request: Request, parent=None, cookie_manager: Optional[CookieManager]=None):
        super().__init__(parent)
        self.request = request
        self._cancelled = False
        self._cookie_manager = cookie_manager
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        self._cancelled = True
        self._cancel_event.set()

    def run(self) -> None:
        try:
            from PyQt6.QtCore import QSettings as _QS
            _s = _QS("Equinox", "Equinox")
            _ph = (_s.value("proxy/host") or "").strip()
            _pp = int(_s.value("proxy/port") or 0)
            
            # Validate proxy settings - both host AND port must be set
            # If port is 0 (the default), proxy is disabled regardless of host
            if not _ph or _pp == 0:
                _proxy = None
                logger.debug("No proxy configured (proxy/host=%r, proxy/port=%r)", _ph or "(empty)", _pp or 0)
            else:
                _proxy = f"http://{_ph}:{_pp}"
                logger.debug("Proxy loaded from settings: %s", _proxy)
                logger.info("Using proxy: %s (if this is unexpected, clear proxy settings in Preferences)", _proxy)
            client = HTTPClient(
                cookie_manager=self._cookie_manager,
                timeout=getattr(self.request, "timeout", DEFAULT_TIMEOUT),
                verify_ssl=getattr(self.request, "verify_ssl", True),
                follow_redirects=getattr(self.request, "follow_redirects", True),
                proxy=_proxy,
                cancel_event=self._cancel_event
            )
            response = client.send(self.request)
            if not self._cancelled:
                self.finished.emit(response)
        except Exception as exc:
            if not self._cancelled:
                self.finished.emit(enrich_exception(exc))


# ─────────────────────────────────────────────────────────────────────────────
# Benchmark worker thread
# ─────────────────────────────────────────────────────────────────────────────

class BenchmarkWorker(QThread):
    """Run the HTTP request loop off the main thread.

    Signals
    -------
    progress(int)   — emitted after each request with the current iteration count.
    finished(list, int) — emitted when done: (elapsed_times_seconds, error_count).
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
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        import time

        times: list = []
        errors = 0

        for i in range(self._n):
            if self._cancelled:
                break
            try:
                client = HTTPClient(
                    cookie_manager=self._cookie_manager,
                    timeout=getattr(self._request, "timeout", DEFAULT_TIMEOUT),
                    verify_ssl=getattr(self._request, "verify_ssl", True),
                    follow_redirects=getattr(self._request, "follow_redirects", True),
                    proxy=self._proxy,
                )
                t0 = time.monotonic()
                client.send(self._request)
                times.append(time.monotonic() - t0)
            except Exception:
                errors += 1
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
        self._worker: Optional[BenchmarkWorker] = None
        self._init_ui()

    def _init_ui(self) -> None:
        from PyQt6.QtWidgets import QProgressBar, QSpinBox as _Spin
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        form = QFormLayout()
        self._count_spin = _Spin()
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

    def _run(self) -> None:
        from PyQt6.QtCore import QSettings

        n = self._count_spin.value()
        self._progress.setMaximum(n)
        self._progress.setValue(0)
        self._progress.setVisible(True)
        self._run_btn.setEnabled(False)
        self._cancel_btn.setEnabled(True)
        self._export_btn.setEnabled(False)
        self._results.setPlainText("Running\u2026")

        # Resolve proxy on the main thread (safe for QSettings on all platforms)
        s = QSettings("Equinox", "Equinox")
        ph = (s.value("proxy/host") or "").strip()
        pp = int(s.value("proxy/port") or 0)
        proxy = f"http://{ph}:{pp}" if (ph and pp > 0) else None

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
            self._worker.cancel()
            self._cancel_btn.setEnabled(False)
            self._results.setPlainText("Cancelling\u2026")

    def _on_progress(self, value: int) -> None:
        self._progress.setValue(value)

    def _on_finished(self, times: list, errors: int) -> None:
        self._run_btn.setEnabled(True)
        self._cancel_btn.setEnabled(False)
        self._progress.setVisible(False)
        self._worker = None

        n = self._progress.maximum()
        if not times:
            self._results.setPlainText(f"All {n} request(s) failed.")
            return

        self._times = times
        self._errors = errors

        times_s = sorted(times)
        n_ok = len(times_s)
        avg = sum(times_s) / n_ok
        p95 = times_s[max(0, int(n_ok * _P95) - 1)]
        p99 = times_s[max(0, int(n_ok * _P99) - 1)]

        self._results.setPlainText(
            f"Requests : {n}\n"
            f"Success  : {n_ok}\n"
            f"Errors   : {errors}\n"
            f"\n"
            f"Min      : {times_s[0] * 1000:.1f} ms\n"
            f"Avg      : {avg * 1000:.1f} ms\n"
            f"Max      : {times_s[-1] * 1000:.1f} ms\n"
            f"p95      : {p95 * 1000:.1f} ms\n"
            f"p99      : {p99 * 1000:.1f} ms\n"
        )
        self._export_btn.setEnabled(True)

    def reject(self) -> None:
        """Cancel any running benchmark before closing."""
        self._cancel()
        super().reject()

    def _export_results(self) -> None:
        """Export benchmark timing data to CSV or JSON chosen by the user."""
        if not self._times:
            return

        import csv
        import json as _json
        from datetime import datetime as _dt

        times_ms = [round(t * 1000, 3) for t in self._times]
        times_s  = sorted(self._times)
        n_ok     = len(times_s)
        avg      = sum(times_s) / n_ok

        summary = {
            "url":      self._request.url,
            "method":   self._request.method,
            "run_at":   _dt.now().isoformat(timespec="seconds"),
            "requests": n_ok + self._errors,
            "success":  n_ok,
            "errors":   self._errors,
            "min_ms":   round(times_s[0] * 1000, 3),
            "avg_ms":   round(avg * 1000, 3),
            "max_ms":   round(times_s[-1] * 1000, 3),
            "p95_ms":   round(times_s[max(0, int(n_ok * _P95) - 1)] * 1000, 3),
            "p99_ms":   round(times_s[max(0, int(n_ok * _P99) - 1)] * 1000, 3),
            "iterations": times_ms,
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
                    for idx, ms in enumerate(times_ms, 1):
                        writer.writerow([idx, ms])
            else:
                with open(path, "w", encoding="utf-8") as f:
                    _json.dump(summary, f, indent=2)
        except Exception as exc:
            QMessageBox.warning(self, "Export Failed", f"Could not write file: {exc}")
