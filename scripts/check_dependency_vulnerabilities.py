#!/usr/bin/env python
"""Run a blocking dependency vulnerability scan against the installed environment."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


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


def _parse_findings(payload: dict[str, Any]) -> list[tuple[str, str, str, str]]:
    """Return normalized vulnerability rows: package, version, vuln_id, fixed_versions."""
    findings: list[tuple[str, str, str, str]] = []
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
    """Execute pip-audit against the currently installed environment and fail on known vulnerabilities."""
    command = [
        sys.executable,
        "-m",
        "pip_audit",
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
            "Dependency vulnerability scan FAILED: pip-audit did not return parseable JSON output.",
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
        print("Resolve by updating dependency bounds in pyproject.toml and reinstalling.")
        return 1

    if result.returncode != 0:
        print("Dependency vulnerability scan FAILED: pip-audit exited non-zero without findings.")
        if result.stdout.strip():
            print(result.stdout.strip())
        if result.stderr.strip():
            print(result.stderr.strip())
        return 1

    print("Dependency vulnerability scan PASSED: no known vulnerabilities in installed packages.")
    return 0


def main() -> int:
    return run_scan()


if __name__ == "__main__":
    sys.exit(main())
