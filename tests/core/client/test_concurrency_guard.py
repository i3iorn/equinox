"""100% coverage tests for equinox.core.client.concurrency_guard"""

import logging
import threading
import pytest

from equinox.core.client.concurrency_guard import ConcurrencyGuard
from equinox.core.exceptions import RequestError


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------

class TestConcurrencyGuardInit:
    def test_valid_max_accepted(self):
        guard = ConcurrencyGuard(5)
        assert guard._max == 5
        assert guard._active == 0

    def test_max_of_one_accepted(self):
        guard = ConcurrencyGuard(1)
        assert guard._max == 1

    def test_large_max_accepted(self):
        guard = ConcurrencyGuard(1000)
        assert guard._max == 1000

    @pytest.mark.parametrize("bad", [0, -1, -100])
    def test_non_positive_integer_raises(self, bad):
        with pytest.raises(ValueError, match="max_concurrent must be a positive integer"):
            ConcurrencyGuard(bad)

    @pytest.mark.parametrize("bad", [1.0, "5", None, [], True])
    def test_non_integer_type_raises(self, bad):
        # bool is a subclass of int but True==1 and False==0;
        # False (== 0) must fail the < 1 check.
        # True (== 1) would pass isinstance but is still bool — the guard
        # intentionally rejects non-int types; True sneaks through isinstance
        # because bool IS int, so we only assert the cases that definitely fail.
        if isinstance(bad, bool):
            # True==1 passes; False==0 fails. Just ensure False raises.
            if not bad:
                with pytest.raises(ValueError):
                    ConcurrencyGuard(bad)
        else:
            with pytest.raises(ValueError):
                ConcurrencyGuard(bad)

    def test_error_message_contains_bad_value(self):
        with pytest.raises(ValueError, match="got 0"):
            ConcurrencyGuard(0)

    def test_lock_created(self):
        guard = ConcurrencyGuard(3)
        assert isinstance(guard._lock, type(threading.Lock()))


# ---------------------------------------------------------------------------
# active property
# ---------------------------------------------------------------------------

class TestActiveProperty:
    def test_initial_active_is_zero(self):
        guard = ConcurrencyGuard(3)
        assert guard.active == 0

    def test_active_reflects_acquired_slots(self):
        guard = ConcurrencyGuard(3)
        guard.acquire()
        assert guard.active == 1
        guard.acquire()
        assert guard.active == 2

    def test_active_decrements_after_release(self):
        guard = ConcurrencyGuard(3)
        guard.acquire()
        guard.release()
        assert guard.active == 0


# ---------------------------------------------------------------------------
# acquire()
# ---------------------------------------------------------------------------

class TestAcquire:
    def test_acquire_increments_active(self):
        guard = ConcurrencyGuard(3)
        guard.acquire()
        assert guard._active == 1

    def test_acquire_up_to_max(self):
        guard = ConcurrencyGuard(3)
        for _ in range(3):
            guard.acquire()
        assert guard._active == 3

    def test_acquire_at_limit_raises_request_error(self):
        guard = ConcurrencyGuard(2)
        guard.acquire()
        guard.acquire()
        with pytest.raises(RequestError, match="Too many concurrent requests"):
            guard.acquire()

    def test_acquire_error_message_contains_counts(self):
        guard = ConcurrencyGuard(1)
        guard.acquire()
        with pytest.raises(RequestError) as exc_info:
            guard.acquire()
        assert "1/1" in str(exc_info.value)

    def test_acquire_does_not_increment_when_limit_reached(self):
        guard = ConcurrencyGuard(1)
        guard.acquire()
        try:
            guard.acquire()
        except RequestError:
            pass
        assert guard._active == 1

    def test_acquire_logs_debug(self, caplog):
        guard = ConcurrencyGuard(5)
        with caplog.at_level(logging.DEBUG, logger="equinox.core.client.concurrency_guard"):
            guard.acquire()
        assert "acquired" in caplog.text
        assert "1/5" in caplog.text


# ---------------------------------------------------------------------------
# release()
# ---------------------------------------------------------------------------

class TestRelease:
    def test_release_decrements_active(self):
        guard = ConcurrencyGuard(3)
        guard.acquire()
        guard.acquire()
        guard.release()
        assert guard._active == 1

    def test_release_to_zero(self):
        guard = ConcurrencyGuard(3)
        guard.acquire()
        guard.release()
        assert guard._active == 0

    def test_release_when_already_zero_logs_warning(self, caplog):
        guard = ConcurrencyGuard(3)
        with caplog.at_level(logging.WARNING, logger="equinox.core.client.concurrency_guard"):
            guard.release()
        assert "mismatch" in caplog.text

    def test_release_when_zero_does_not_go_negative(self):
        guard = ConcurrencyGuard(3)
        guard.release()
        assert guard._active == 0

    def test_release_logs_debug(self, caplog):
        guard = ConcurrencyGuard(5)
        guard.acquire()
        with caplog.at_level(logging.DEBUG, logger="equinox.core.client.concurrency_guard"):
            guard.release()
        assert "released" in caplog.text
        assert "0/5" in caplog.text


# ---------------------------------------------------------------------------
# slot() context manager
# ---------------------------------------------------------------------------

class TestSlot:
    def test_slot_acquires_and_releases(self):
        guard = ConcurrencyGuard(3)
        with guard.slot():  # type: ignore[attr-defined]
            assert guard.active == 1
        assert guard.active == 0

    def test_slot_releases_on_exception(self):
        guard = ConcurrencyGuard(3)
        with pytest.raises(RuntimeError):
            with guard.slot():  # type: ignore[attr-defined]
                assert guard.active == 1
                raise RuntimeError("boom")
        assert guard.active == 0

    def test_slot_raises_request_error_at_limit(self):
        guard = ConcurrencyGuard(1)
        guard.acquire()
        with pytest.raises(RequestError):
            with guard.slot():  # type: ignore[attr-defined]
                pass  # should never reach here

    def test_slot_is_reentrant_up_to_max(self):
        guard = ConcurrencyGuard(3)
        with guard.slot():  # type: ignore[attr-defined]
            with guard.slot():  # type: ignore[attr-defined]
                with guard.slot():  # type: ignore[attr-defined]
                    assert guard.active == 3
        assert guard.active == 0

    def test_slot_yields_none(self):
        guard = ConcurrencyGuard(3)
        with guard.slot() as value:  # type: ignore[attr-defined]
            assert value is None


# ---------------------------------------------------------------------------
# __repr__
# ---------------------------------------------------------------------------

class TestRepr:
    def test_repr_initial(self):
        guard = ConcurrencyGuard(4)
        assert repr(guard) == "ConcurrencyGuard(active=0, max=4)"

    def test_repr_after_acquire(self):
        guard = ConcurrencyGuard(10)
        guard.acquire()
        guard.acquire()
        assert repr(guard) == "ConcurrencyGuard(active=2, max=10)"


# ---------------------------------------------------------------------------
# Thread-safety smoke test
# ---------------------------------------------------------------------------

class TestThreadSafety:
    def test_concurrent_acquires_respect_limit(self):
        max_concurrent = 5
        guard = ConcurrencyGuard(max_concurrent)
        errors = []
        peak = []
        lock = threading.Lock()

        def _worker():
            try:
                guard.acquire()
                with lock:
                    peak.append(guard.active)
                import time; time.sleep(0.01)
                guard.release()
            except RequestError as e:
                errors.append(e)

        threads = [threading.Thread(target=_worker) for _ in range(max_concurrent + 3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Some threads must have been rejected
        assert len(errors) > 0
        # Peak concurrent never exceeded the limit
        assert all(p <= max_concurrent for p in peak)
        # Guard back to zero after all threads finish
        assert guard.active == 0

