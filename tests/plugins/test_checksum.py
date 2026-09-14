"""Tests for plugin checksum calculation and verification."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from equinox.core.exceptions import SecurityError
from equinox.plugins.security import calculate_checksum, verify_checksum


class TestCalculateChecksum:
    def test_deterministic(self, tmp_path: Path) -> None:
        p = tmp_path / "plugin.py"
        p.write_text("x = 1\n", encoding="utf-8")
        c1 = calculate_checksum(p)
        c2 = calculate_checksum(p)
        assert c1 == c2

    def test_matches_sha256(self, tmp_path: Path) -> None:
        p = tmp_path / "plugin.py"
        content = b"hello world\n"
        p.write_bytes(content)
        expected = hashlib.sha256(content).hexdigest()
        assert calculate_checksum(p) == expected

    def test_different_files_differ(self, tmp_path: Path) -> None:
        p1 = tmp_path / "a.py"
        p2 = tmp_path / "b.py"
        p1.write_text("x = 1\n", encoding="utf-8")
        p2.write_text("x = 2\n", encoding="utf-8")
        assert calculate_checksum(p1) != calculate_checksum(p2)


class TestVerifyChecksum:
    def test_matching_checksum_returns_true(self, tmp_path: Path) -> None:
        p = tmp_path / "plugin.py"
        p.write_text("x = 1\n", encoding="utf-8")
        expected = calculate_checksum(p)
        assert verify_checksum(p, expected) is True

    def test_mismatched_checksum_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "plugin.py"
        p.write_text("x = 1\n", encoding="utf-8")
        with pytest.raises(SecurityError, match="checksum mismatch"):
            verify_checksum(p, "0000000000000000000000000000000000000000000000000000000000000000")
