#!/usr/bin/env python
"""Run a blocking dependency vulnerability scan against the installed environment."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

# Advisories with no upstream fix, whose vulnerable code path this project
# demonstrably never exercises. Keep this minimal: each entry must carry the
# reason, and a waiver only applies while the advisory is genuinely unpatched
# ("fixed in: none listed") -- once a fix is published the entry stops
# applying and the scan fails until the dependency is upgraded.
_WAIVED_VULNS: dict[str, str] = {
    "PYSEC-2026-3740": (
        "nltk pathsec model import/export sandbox bypass (CVE-2026-81726, "
        "GHSA-8mgp-746c-j5xp): no upstream fix; equally, this project never "
        "imports nltk, so the vulnerable APIs are unreachable"
    ),
}


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
        # Equinox itself is always installed editable (`pip install -e .`) and
        # is never published to PyPI, so pip-audit can't look it up.
        "--skip-editable",
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
    waived: list[tuple[str, str, str, str]] = []
    blocking: list[tuple[str, str, str, str]] = []
    for row in findings:
        package, version, vuln_id, fixed = row
        reason = _WAIVED_VULNS.get(vuln_id)
        if reason is not None and fixed == "none listed":
            waived.append(row)
        else:
            blocking.append(row)

    for package, version, vuln_id, _fixed in waived:
        print(f"WAIVED (not blocking): {package}=={version}: {vuln_id} - {_WAIVED_VULNS[vuln_id]}")

    if blocking:
        print("Dependency vulnerability scan FAILED: known vulnerabilities detected.")
        for package, version, vuln_id, fixed in blocking:
            print(f"  - {package}=={version}: {vuln_id} (fixed in: {fixed})")
        print("Resolve by updating dependency bounds in pyproject.toml and reinstalling.")
        return 1

    # pip-audit exits non-zero whenever it reports any vulnerability, so a
    # non-zero exit is only an audit-tool failure when it produced no findings
    # at all. If every finding was waived, the output above explains why.
    if result.returncode != 0 and not findings:
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
