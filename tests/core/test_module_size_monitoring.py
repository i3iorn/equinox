"""Guards against uncontrolled growth in high-risk core modules."""

from pathlib import Path


def _line_count(path: Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines())


def test_phase2_monitoring_targets_stay_within_size_budget() -> None:
    """Phase 2.4: keep medium-priority modules from regressing in size."""
    root = Path(__file__).resolve().parents[2] / "src" / "equinox" / "core"

    budgets = {
        root / "log_setup.py": 450,
        root / "response_intelligence" / "consistency.py": 500,
        root / "client" / "dispatcher.py": 500,
        root / "client" / "http_client.py": 400,
        root / "client" / "retry_policy.py": 340,
    }

    violations = []
    for file_path, max_lines in budgets.items():
        count = _line_count(file_path)
        if count > max_lines:
            violations.append(f"{file_path.relative_to(root.parent.parent)}: {count} > {max_lines}")

    assert not violations, "\n".join(["Module size budget exceeded:"] + violations)
