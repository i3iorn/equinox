# Changelog — Equinox v0.4.6

**Release Date:** May 20, 2026

## Overview

Equinox v0.4.6 completes phases 14-17 of the GUI service-boundary refactor plan. This release focuses on plugin trust-model clarity, in-process policy-boundary hardening, architecture guard tests, and documentation alignment so boundary regressions are less likely to return.

---

## [0.4.6] — 2026-05-20

### Architecture

#### Clarified: Plugin Trust Model and Security Language
- **Files:** `src/equinox/plugins/manager.py`, `src/equinox/plugins/security.py`, `src/equinox/plugins/base.py`
- **Change:** Plugin architecture wording now explicitly states plugins are trusted local in-process extensions.
- **Details:** Permission checks, checksums, allowlists, and resource guards are documented as policy controls rather than hard isolation.
- **Benefit:** Removes ambiguity and aligns implementation and docs with the actual trust boundary.

#### Improved: Plugin Context Boundary Tightening
- **File:** `src/equinox/plugins/security.py`
- **Change:** `SecurePluginContext` now exposes intentional guarded proxies with clearer context encapsulation.
- **Details:** Storage and HTTP access remain permission-gated while reducing accidental raw-handle exposure paths.
- **Benefit:** Stronger internal boundary discipline for plugin capabilities.

#### Improved: Consistent Plugin Hook Failure Auditing
- **File:** `src/equinox/plugins/manager.py`
- **Change:** Added centralized plugin-hook failure logging/auditing path.
- **Details:** `on_request`, `on_response`, and `on_error` failures now emit consistent warning/audit events.
- **Benefit:** Better observability and easier operations/debugging for plugin runtime issues.

### Testing

#### Added: Architecture Boundary Guard Suite
- **File:** `tests/core/test_architecture_boundaries.py` (new)
- **Coverage:**
  - request service modules do not import Qt
  - critical GUI boundary modules do not import forbidden raw storage managers
  - banned GUI modules do not reach through `mgr.db`
  - plugin modules do not claim unsupported hard sandbox isolation
- **Benefit:** Prevents recurrence of key boundary leaks.

#### Added: Targeted Service Helper Regressions
- **Files:**
  - `tests/application/requests/test_preparation.py`
  - `tests/application/requests/test_post_processing.py`
- **Coverage:** auth interpolation fail-safe behavior and post-script error-path expectations.
- **Benefit:** Hardens behavior of moved request helper logic.

#### Enhanced: Plugin Loader Security Tests
- **File:** `tests/core/test_plugin_loader_security.py`
- **Change:** Added regression test to verify consistent audit events on plugin hook failures.
- **Benefit:** Locks in improved plugin-failure telemetry behavior.

### Documentation

#### Updated: Contributor and Architecture Boundary Guidance
- **Files:** `README.md`, `AGENTS.md`, `docs/gui_service_boundary_refactor_plan.md`
- **Change:** Added explicit plugin trust-model notes, clearer placement rules for new code, and completed phase 14-17 plan checklist.
- **Benefit:** Keeps implementation and contributor guidance aligned with current architecture.

### For Upgrading

- No user-facing breaking changes.
- For plugin maintainers and internal extension authors:
  - Treat plugin execution as trusted in-process extension behavior, not a hard security sandbox.
  - Review hook error monitoring to consume the standardized plugin-hook audit events.
  - Keep GUI code on facade/service boundaries and avoid direct storage-manager construction.

---

**Total Changes (high-level):**
- Completed phases 14-17 (plugin trust clarity, boundary hardening, guard tests, docs cleanup).
- Added architecture guard tests to enforce service/UI/plugin boundary expectations.
- Improved plugin hook failure observability with consistent audit logging.
- Synchronized contributor docs and refactor plan checklists with implemented boundaries.

