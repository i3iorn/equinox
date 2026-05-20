from pathlib import Path

import pytest

from equinox.gui.file_ops import (
    atomic_write_bytes,
    safe_read_text_file,
    validate_selected_path,
)


def test_validate_selected_path_requires_existing_file(tmp_path: Path) -> None:
    missing = tmp_path / "missing.txt"
    with pytest.raises(ValueError, match="does not exist"):
        validate_selected_path(str(missing), must_exist=True)


def test_safe_read_text_file_rejects_oversize(tmp_path: Path) -> None:
    source = tmp_path / "data.env"
    source.write_text("A" * 32, encoding="utf-8")

    with pytest.raises(ValueError, match="too large"):
        safe_read_text_file(source, max_bytes=8)


def test_atomic_write_bytes_writes_payload(tmp_path: Path) -> None:
    target = tmp_path / "response.bin"
    atomic_write_bytes(target, b"abc123")

    assert target.read_bytes() == b"abc123"
    assert validate_selected_path(str(target), must_exist=True) == target
