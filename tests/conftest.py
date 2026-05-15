import os
import sys
import uuid
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"

if str(_SRC) not in sys.path:
	sys.path.insert(0, str(_SRC))

# Force Qt into offscreen/platformless mode for tests so dialogs don't require
# a display. Must be set before importing PyQt widgets.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

# Ensure a single QApplication exists for the duration of the test session.
_APP = QApplication.instance() or QApplication([])


# ──────────────────────────────────────────────────────────────────────────────
# Test-scoped environment isolation fixtures
# ──────────────────────────────────────────────────────────────────────────────

import pytest


@pytest.fixture(scope="session")
def test_master_password() -> str:
    """Provide a test-specific master password (session scope).

    Generates a unique password per test session to prevent cross-contamination.
    """
    return f"test-pwd-{uuid.uuid4().hex[:12]}"


@pytest.fixture(autouse=True)
def isolated_environment(test_master_password: str, monkeypatch) -> None:
    """Automatically isolate environment for each test.

    This fixture:
    1. Sets a unique master password for this test
    2. Creates an isolated temporary directory
    3. Resets history capture state after test

    Applied automatically to all tests (autouse=True).
    """
    # Create isolated temporary home directory
    with tempfile.TemporaryDirectory() as tmp_home:
        test_home = Path(tmp_home) / ".equinox"
        test_home.mkdir()

        monkeypatch.setenv("EQUINOX_HOME", str(test_home))
        monkeypatch.setenv("EQUINOX_MASTER_PASSWORD", test_master_password)
        monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")

        yield

        # Cleanup: reset history capture state to prevent leaks
        try:
            from equinox.core.history_config import set_capture_bodies
            set_capture_bodies(True)
        except Exception:
            pass  # Module may not be imported


@pytest.fixture
def temp_equinox_home(tmp_path: Path, monkeypatch) -> Path:
    """Provide an isolated ~/.equinox directory for a test.

    Use this when you need explicit control over the temporary home directory.

    Args:
        tmp_path: pytest's built-in temporary directory fixture
        monkeypatch: pytest's monkeypatch fixture

    Returns:
        Path to temporary .equinox directory
    """
    home = tmp_path / ".equinox"
    home.mkdir()
    (home / "logs").mkdir(exist_ok=True)
    (home / "plugins").mkdir(exist_ok=True)
    monkeypatch.setenv("EQUINOX_HOME", str(home))
    return home


@pytest.fixture
def temp_database(temp_equinox_home: Path):
    """Provide a fresh, empty database for testing.

    Args:
        temp_equinox_home: pytest fixture providing isolated home directory

    Returns:
        Database instance backed by a temporary file
    """
    from equinox.storage.database import Database

    db_path = temp_equinox_home / "test.db"
    db = Database(str(db_path))
    yield db


@pytest.fixture
def history_capture_disabled():
    """Context manager to safely disable history capture for a test.

    Automatically restores prior state on exit, preventing test isolation leaks.

    Example:
        def test_without_history_capture(history_capture_disabled):
            with history_capture_disabled():
                # history NOT captured here
                result = client.send(request)
            # prior capture state restored
    """
    from contextlib import contextmanager

    @contextmanager
    def _context():
        from equinox.core.history_config import set_capture_bodies, is_capture_enabled

        old_state = is_capture_enabled()
        try:
            set_capture_bodies(False)
            yield
        finally:
            set_capture_bodies(old_state)

    return _context


# ──────────────────────────────────────────────────────────────────────────────
# Public exports for use in test modules
# ──────────────────────────────────────────────────────────────────────────────

__all__ = [
    "isolated_environment",
    "temp_equinox_home",
    "temp_database",
    "history_capture_disabled",
    "test_master_password",
]


