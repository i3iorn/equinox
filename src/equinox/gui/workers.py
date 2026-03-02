"""Background worker threads and dialogs for the Equinox GUI."""

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
from equinox.core.request import Request, Response
from equinox.core.error_enrichment import RichError, enrich_exception
from equinox.gui.theme import get_mono_font

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
                self.done.emit(False, f"HTTP {resp.status_code}: {err}")
        except Exception as exc:
            self.done.emit(False, str(exc))


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

    def __init__(self, request: Request, parent=None, cookie_manager=None):
        super().__init__(parent)
        self.request = request
        self._cancelled = False
        self._cookie_manager = cookie_manager

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        try:
            from PyQt6.QtCore import QSettings as _QS
            _s = _QS("Equinox", "Equinox")
            _ph = (_s.value("proxy/host") or "").strip()
            _pp = int(_s.value("proxy/port") or 0)
            _proxy = f"http://{_ph}:{_pp}" if _ph and _pp else None
            client = HTTPClient(
                cookie_manager=self._cookie_manager,
                timeout=getattr(self.request, "timeout", DEFAULT_TIMEOUT),
                verify_ssl=getattr(self.request, "verify_ssl", True),
                follow_redirects=getattr(self.request, "follow_redirects", True),
                proxy=_proxy,
            )
            response = client.send(self.request)
            if not self._cancelled:
                self.finished.emit(response)
        except Exception as exc:
            if not self._cancelled:
                self.finished.emit(enrich_exception(exc))


# ─────────────────────────────────────────────────────────────────────────────
# Benchmark dialog
# ─────────────────────────────────────────────────────────────────────────────

class BenchmarkDialog(QDialog):
    """Run the current request N times and display timing statistics."""

    def __init__(self, request: Request, parent=None, cookie_manager=None):
        super().__init__(parent)
        self._request = request
        self._cookie_manager = cookie_manager
        self.setWindowTitle("Benchmark")
        self.setMinimumSize(420, 340)
        self._times: list = []
        self._errors: int = 0
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

        self._run_btn = QPushButton("Run Benchmark")
        self._run_btn.clicked.connect(self._run)
        layout.addWidget(self._run_btn)

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
        import time
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtCore import QSettings

        n = self._count_spin.value()
        self._progress.setMaximum(n)
        self._progress.setValue(0)
        self._progress.setVisible(True)
        self._run_btn.setEnabled(False)
        self._results.setPlainText("Running\u2026")
        QApplication.processEvents()

        s = QSettings("Equinox", "Equinox")
        ph = (s.value("proxy/host") or "").strip()
        pp = int(s.value("proxy/port") or 0)
        proxy = f"http://{ph}:{pp}" if ph and pp else None

        times: list = []
        errors = 0

        for i in range(n):
            try:
                client = HTTPClient(
                    cookie_manager=self._cookie_manager,
                    timeout=getattr(self._request, "timeout", DEFAULT_TIMEOUT),
                    verify_ssl=getattr(self._request, "verify_ssl", True),
                    follow_redirects=getattr(self._request, "follow_redirects", True),
                    proxy=proxy,
                )
                t0 = time.monotonic()
                client.send(self._request)
                times.append(time.monotonic() - t0)
            except Exception:
                errors += 1
            self._progress.setValue(i + 1)
            QApplication.processEvents()

        self._run_btn.setEnabled(True)
        self._progress.setVisible(False)

        if not times:
            self._results.setPlainText(f"All {n} request(s) failed.")
            return

        self._times = times
        self._errors = errors

        times_s = sorted(times)
        n_ok = len(times_s)
        avg = sum(times_s) / n_ok
        p95 = times_s[max(0, int(n_ok * 0.95) - 1)]
        p99 = times_s[max(0, int(n_ok * 0.99) - 1)]

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
            "p95_ms":   round(times_s[max(0, int(n_ok * 0.95) - 1)] * 1000, 3),
            "p99_ms":   round(times_s[max(0, int(n_ok * 0.99) - 1)] * 1000, 3),
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
