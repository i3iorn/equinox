#!/usr/bin/env python
"""Run a blocking dependency vulnerability scan against the committed lockfile."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = ROOT / "requirements-lock.txt"


def _extract_json_payload(raw_output: str) -> str:
    """Extract the JSON object from pip-audit output."""
    for line in reversed(raw_output.splitlines()):
        candidate = line.strip()
        if candidate.startswith("{") and candidate.endswith("}"):
            return candidate

    stripped = raw_output.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        return stripped
    return ""


def _parse_findings(payload: Dict[str, Any]) -> List[Tuple[str, str, str, str]]:
    """Return normalized vulnerability rows: package, version, vuln_id, fixed_versions."""
    findings: List[Tuple[str, str, str, str]] = []
    for dependency in payload.get("dependencies", []):
        package = str(dependency.get("name", "<unknown>"))
        version = str(dependency.get("version", "<unknown>"))
        for vuln in dependency.get("vulns", []):
            vuln_id = str(vuln.get("id", "<unknown>"))
            fix_versions = vuln.get("fix_versions") or []
            fixed = ", ".join(str(v) for v in fix_versions) if fix_versions else "none listed"
            findings.append((package, version, vuln_id, fixed))
    return findings


def run_scan() -> int:
    """Execute pip-audit for requirements-lock.txt and fail on known vulnerabilities."""
    if not LOCK_PATH.exists():
        print("Dependency vulnerability scan FAILED: missing requirements-lock.txt")
        print("Generate it with: py -3 scripts/manage_requirements_lock.py --write")
        return 1

    command = [
        sys.executable,
        "-m",
        "pip_audit",
        "-r",
        str(LOCK_PATH),
        "--format",
        "json",
        "--progress-spinner",
        "off",
        "--strict",
    ]
    result = subprocess.run(
        command,
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        check=False,
    )

    payload_text = _extract_json_payload(result.stdout) or _extract_json_payload(result.stderr)
    if not payload_text:
        print(
            "Dependency vulnerability scan FAILED: pip-audit did not return parseable JSON output."
        )
        if result.stdout.strip():
            print(result.stdout.strip())
        if result.stderr.strip():
            print(result.stderr.strip())
        return 1

    try:
        report = json.loads(payload_text)
    except json.JSONDecodeError as exc:
        print(f"Dependency vulnerability scan FAILED: invalid JSON output ({exc}).")
        if result.stdout.strip():
            print(result.stdout.strip())
        if result.stderr.strip():
            print(result.stderr.strip())
        return 1

    findings = _parse_findings(report)
    if findings:
        print("Dependency vulnerability scan FAILED: known vulnerabilities detected.")
        for package, version, vuln_id, fixed in findings:
            print(f"  - {package}=={version}: {vuln_id} (fixed in: {fixed})")
        print("Resolve by updating dependency bounds in pyproject.toml, then regenerate lockfile.")
        print("  py -3 scripts/manage_requirements_lock.py --write")
        return 1

    if result.returncode != 0:
        print("Dependency vulnerability scan FAILED: pip-audit exited non-zero without findings.")
        if result.stdout.strip():
            print(result.stdout.strip())
        if result.stderr.strip():
            print(result.stderr.strip())
        return 1

    print(
        "Dependency vulnerability scan PASSED: no known vulnerabilities in requirements-lock.txt."
    )
    return 0


def main() -> int:
    return run_scan()


if __name__ == "__main__":
    sys.exit(main())
