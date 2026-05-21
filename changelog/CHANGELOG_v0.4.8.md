# Changelog — Equinox v0.4.8

**Release Date:** May 21, 2026

## Overview

Equinox v0.4.8 focuses on developer workflow reliability and quality hardening. This release adds targeted pre-commit test selection, improves lockfile validation behavior, expands coverage-focused tests, and includes broad typing and maintenance cleanups.

---

## [0.4.8] — 2026-05-21

### Tooling & Quality

#### Added: Affected-test runner for pre-commit
- **Files:** `.pre-commit-config.yaml`, `scripts/run_affected_tests.py`, `tests/scripts/test_run_affected_tests.py`
- **Change:** Added a local pre-commit hook and runner that selects tests relevant to staged module changes and enforces configured coverage thresholds.
- **Benefit:** Speeds up local feedback while preserving coverage gates.

#### Updated: Lockfile validation behavior
- **File:** `scripts/manage_requirements_lock.py`
- **Change:** Aligned `--write` and `--check` behavior to avoid inconsistent lock validation outcomes.
- **Benefit:** Prevents recurring false-negative pre-commit failures on `requirements-lock-current`.

#### Updated: Coverage policy configuration
- **File:** `pyproject.toml`
- **Change:** Raised coverage `fail_under` to `87` and kept strict GUI omission in coverage run settings.
- **Benefit:** Tightens quality expectations while keeping coverage checks practical for non-GUI code paths.

### Typing & Stability

#### Improved: Type-safety and import consistency across core modules
- **Files (representative):**
  `src/equinox/auth/_api_key.py`,
  `src/equinox/auth/_base.py`,
  `src/equinox/auth/_basic.py`,
  `src/equinox/auth/_factory.py`,
  `src/equinox/core/client/auth_applier.py`,
  `src/equinox/core/client/http_client.py`,
  `src/equinox/core/exceptions.py`,
  `src/equinox/gui/dialogs/auth_dialog.py`,
  `src/equinox/gui/request_panel/mixins/_send_mixin.py`,
  `src/equinox/gui/response_panel/intelligence_panel.py`
- **Change:** Applied targeted typing cleanups and import-order normalization, with small supporting maintenance fixes.
- **Benefit:** Reduces static-analysis noise and improves long-term maintainability.

### Tests & Coverage

#### Added: New coverage-focused test suites
- **Files (representative):**
  `tests/application/collections/test_facade_coverage.py`,
  `tests/application/history/test_history_facade_coverage.py`,
  `tests/application/requests/test_assembly.py`,
  `tests/application/requests/test_execution_coverage.py`,
  `tests/application/requests/test_history_coverage.py`,
  `tests/application/requests/test_persistence_coverage.py`,
  `tests/application/requests/test_post_processing_coverage.py`,
  `tests/core/test_request_defaults.py`,
  `tests/core/test_response_intelligence_shared.py`,
  `tests/core/test_scripts_coverage.py`,
  `tests/core/test_secret_managers_coverage.py`,
  `tests/core/test_security_serialization_coverage.py`,
  `tests/core/test_urls_coverage.py`,
  `tests/core/test_validation_guards_limits_patterns.py`,
  `tests/test_exporters_coverage.py`
- **Change:** Introduced and expanded targeted tests for previously under-covered modules and flows.
- **Benefit:** Improves defect detection and confidence in refactors.

### Documentation

#### Updated: Project docs and workflow pointers
- **Files:** `README.md`, `WORKFLOW.md`
- **Change:** Refreshed documentation and updated version/changelog pointers to the latest release.
- **Benefit:** Keeps contributor guidance aligned with current tooling and release state.

---

**Total Changes (high-level):**
- Added affected-test selection and coverage enforcement in pre-commit.
- Stabilized requirements lockfile check behavior.
- Increased coverage threshold and expanded coverage-oriented tests.
- Applied broad typing/import consistency improvements and doc updates.
