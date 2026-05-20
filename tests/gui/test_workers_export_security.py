"""Security and robustness tests for GUI benchmark export helpers."""

from __future__ import annotations

from pathlib import Path

import equinox.gui.workers as workers_mod

_validate_export_path = workers_mod._validate_export_path
_atomic_write_text = workers_mod._atomic_write_text


def test_validate_export_path_rejects_null_byte() -> None:
    assert _validate_export_path("bad\x00name.json") is None


def test_validate_export_path_rejects_missing_parent(tmp_path: Path) -> None:
    missing = tmp_path / "missing" / "out.json"
    assert _validate_export_path(str(missing)) is None


def test_validate_export_path_accepts_existing_parent(tmp_path: Path) -> None:
    target = tmp_path / "out.json"
    resolved = _validate_export_path(str(target))
    assert resolved == target


def test_atomic_write_text_replaces_existing_content(tmp_path: Path) -> None:
    target = tmp_path / "results.json"
    target.write_text("old", encoding="utf-8")

    _atomic_write_text(target, "new-content")

    assert target.read_text(encoding="utf-8") == "new-content"
