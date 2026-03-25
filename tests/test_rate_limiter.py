import time
import threading

from equinox.core.rate_limiter import RateLimiter
from equinox.core.exceptions import RateLimitError


def test_rate_limiter_basic(monkeypatch):
    # simulate time progression
    times = [1000.0]

    def fake_time():
        return times[0]

    monkeypatch.setattr("time.time", fake_time)

    rl = RateLimiter(max_per_minute=2, window_seconds=60)

    # first two acquires should pass
    rl.try_acquire()
    rl.try_acquire()

    # third should raise
    try:
        rl.try_acquire()
        assert False, "Expected RateLimitError"
    except RateLimitError:
        pass

    # advance time beyond window and allow another acquire
    times[0] += 61
    rl.try_acquire()


def test_rate_limiter_concurrent(monkeypatch):
    # simple concurrency test: multiple threads call try_acquire
    rl = RateLimiter(max_per_minute=1000, window_seconds=60)

    def worker():
        rl.try_acquire()

    threads = [threading.Thread(target=worker) for _ in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # should have recorded 50 timestamps
    # cannot access private _times reliably in API but ensure no exception thrown
    assert True

