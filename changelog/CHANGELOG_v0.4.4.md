# Changelog — Equinox v0.4.4

**Release Date:** May 19, 2026

## Overview

Equinox v0.4.4 focuses on the first half of the GUI service-boundary refactor. The core request-send orchestration, post-processing decisions, and request persistence flows were moved behind application-layer services/facades so GUI mixins remain presentation-focused. This release also includes compatibility fixes discovered during migration and broad test updates to keep behavior stable.

---

## [0.4.4] — 2026-05-19

### Architecture

#### Added: Request Execution Orchestration Service Coverage (Phase 5)
- **Files:** `src/equinox/application/requests/execution.py`, `tests/application/requests/test_execution.py`
- **Change:** Request-send preparation logic is verified through application-layer orchestration contracts.
- **Details:** Added tests for transport-ready request construction, unresolved-placeholder blocking, and strict pre-script policy behavior.
- **Benefit:** GUI send flow can delegate to a stable service boundary with deterministic outcomes.

#### Added: Post-Processing Decision Service (Phase 6)
- **Files:** `src/equinox/application/requests/post_processing.py`, `src/equinox/application/requests/__init__.py`
- **Change:** Introduced explicit result/plan DTOs for post-send behavior.
- **New DTOs and planners:**
  - `CaptureProcessingOutcome`
  - `PostScriptOutcome`
  - `DeferredPersistencePlan`
  - `ErrorHandlingPlan`
  - `SuccessHandlingPlan`
- **New functions:**
  - `apply_captures(...)`
  - `run_post_script(...)`
  - `build_deferred_persistence_plan(...)`
  - `build_error_handling_plan(...)`
  - `build_success_handling_plan(...)`
- **Benefit:** Moves branching/business decisions out of GUI handlers into testable pure functions.

#### Added: Request Persistence Facade (Phase 7)
- **Files:** `src/equinox/application/requests/persistence.py`, `src/equinox/gui/request_panel/panel.py`
- **Change:** Request persistence operations now route through `RequestPersistenceFacade`.
- **Capabilities added:**
  - Save/update/autosave routing
  - Explicit auth token persistence methods
  - Save-dialog decision result object (`SaveRequestResult`)
  - Collection listing for save flow (`list_save_collections`)
- **Benefit:** Eliminates direct persistence-manager coupling from request panel mixins.

#### Added: Request History Service Boundary
- **Files:** `src/equinox/application/requests/history.py`, `src/equinox/gui/request_panel/mixins/_send_mixin.py`
- **Change:** History-save and URL-completion concerns are delegated via application services.
- **Benefit:** Cleaner separation between UI interaction and storage/domain responsibilities.

### GUI Refactor

#### Improved: Save Dialog Is UI-Only (Phase 8)
- **Files:** `src/equinox/gui/request_panel/save_dialog.py`, `src/equinox/gui/request_panel/save_flow_mixin.py`
- **Change:** `SaveRequestDialog` no longer loads collections from the DB.
- **Details:** Dialog now receives plain collection choices and returns validated user input only.
- **Added typing:** `SaveDialogCollectionChoice` `TypedDict` for clearer dialog contracts.
- **Benefit:** Dialog is now side-effect free and easier to test in isolation.

#### Refactored: Request Panel Mixins to Facade-Oriented Calls
- **Files:**
  - `src/equinox/gui/request_panel/mixins/autosave.py`
  - `src/equinox/gui/request_panel/save_flow_mixin.py`
  - `src/equinox/gui/request_panel/mixins/_auth_mixin.py`
  - `src/equinox/gui/request_panel/mixins/_send_mixin.py`
- **Change:** Mixins call injected facade/service methods instead of directly constructing/managing storage managers.
- **Benefit:** Improved maintainability and lower coupling in GUI request flows.

### Fixed

#### Fixed: Audit Logger Compatibility in Request Pipeline
- **File:** `src/equinox/core/client/pipeline.py`
- **Issue:** Some audit logger test doubles raised `TypeError` for unsupported `request_id` kwarg.
- **Fix:** Added signature-aware compatibility wrapper before calling `log_request(...)`.
- **Benefit:** Backward-compatible audit calls across legacy/new logger signatures.

#### Fixed: Inherited Auth Resolution Regression
- **File:** `src/equinox/gui/request_panel/mixins/_auth_mixin.py`
- **Issue:** GUI could fail to surface inherited collection auth for loaded requests.
- **Fix:** Added `_build_auth_probe()` to safely resolve inherited auth context.
- **Benefit:** Correct inherited-auth behavior restored in request editor UI.

#### Fixed: Save-Flow Test Harness Drift
- **File:** `tests/gui/test_request_panel_extra.py`
- **Issue:** Mock panel lacked `_build_request_editor_snapshot()` after save-flow refactor.
- **Fix:** Updated test doubles to include the required snapshot API.
- **Benefit:** Save/update tests now align with refactored boundary contracts.

### Testing

#### Added: New Service-Level Request Tests
- **Files:**
  - `tests/application/requests/test_execution.py`
  - `tests/application/requests/test_post_processing.py`
  - `tests/application/requests/test_persistence.py`
- **Coverage:** send preparation, post-send planners, persistence decision routing, auth token persistence paths.

#### Updated: GUI Tests for New Boundaries
- **Files:**
  - `tests/gui/test_request_interpolation_chain.py`
  - `tests/gui/test_request_panel_extra.py`
  - `tests/gui/test_gui_dialogs_coverage.py`
- **Change:** Migrated patch points/assertions to application services and facade contracts.

### For Upgrading

- No user-facing breaking changes.
- For custom GUI extensions/tests:
  - Prefer `application.requests` facade/service APIs over direct storage manager calls.
  - Treat `SaveRequestDialog` as UI-only and provide collection choices from caller side.
  - Update mocks to include snapshot/facade collaborators used by refactored mixins.

---

**Total Changes (high-level):**
- Introduced request-layer post-processing and persistence boundary modules.
- Refactored request panel flows to injected services/facades.
- Added broad service-level test coverage for migrated request logic.
- Fixed multiple migration regressions without changing intended runtime behavior.

