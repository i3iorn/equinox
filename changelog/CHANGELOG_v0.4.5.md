# Changelog — Equinox v0.4.5

**Release Date:** May 20, 2026

## Overview

Equinox v0.4.5 completes the second half of the GUI service-boundary refactor. Collection and history GUI flows now route through application facades, and user-facing error dialogs are centralized through `ErrorPresenter`. The release keeps behavior stable while making GUI modules thinner, more testable, and easier to evolve.

---

## [0.4.5] — 2026-05-20

### Architecture

#### Added: Collection Facade Boundary
- **Files:** `src/equinox/application/collections/facade.py`, `src/equinox/application/collections/__init__.py`, `src/equinox/application/__init__.py`
- **Change:** Introduced `CollectionFacade` as the application-layer API for collection-tree operations.
- **Details:** CRUD, move/sort/reorder, auth operations, and request-location lookups are handled without GUI reach-through to storage internals.
- **Benefit:** Removes direct storage coupling from collection UI actions and centralizes business decisions.

#### Added: History Facade Boundary
- **Files:** `src/equinox/application/history/facade.py`, `src/equinox/application/history/__init__.py`
- **Change:** Introduced `HistoryFacade` for history query, stats, deletion, clear, and history-entry mapping.
- **Benefit:** Main window and history panel can orchestrate through one stable service interface.

#### Added: Centralized GUI Error Presenter
- **File:** `src/equinox/gui/error_presenter.py`
- **Change:** Added `ErrorPresenter` for standardized warning/error/info/request-failure dialogs.
- **Methods:**
  - `warning(...)`
  - `error(...)`
  - `info(...)`
  - `request_failure(...)`
  - `show_status(...)`
- **Benefit:** Uniform user messaging and reduced dialog duplication across GUI modules.

### GUI Refactor

#### Refactored: Collections Panel Actions to Facade Calls
- **Files:** `src/equinox/gui/collection_panel/actions.py`, `src/equinox/gui/collection_panel/panel.py`
- **Change:** Collection panel action handlers now use `_collection_facade` for operational decisions.
- **Benefit:** Clear boundary between UI event handling and collection domain behavior.

#### Refactored: History Panel and Main Window to HistoryFacade
- **Files:**
  - `src/equinox/gui/history_panel.py`
  - `src/equinox/gui/window/__init__.py`
  - `src/equinox/gui/window/_panels.py`
  - `src/equinox/gui/window/_history.py`
- **Change:** Shared history facade is injected and used for retrieval/mapping/replay flows.
- **Benefit:** Consistent history behavior and simpler testing across panels/window mixins.

#### Refactored: Request Send Error Dialogs to ErrorPresenter
- **File:** `src/equinox/gui/request_panel/mixins/_send_mixin.py`
- **Change:** Replaced direct `QMessageBox`/manual copyable-error dialog calls in send flow with `ErrorPresenter` APIs.
- **Scenarios migrated:** Missing URL, strict policy blocks, unresolved blocking issues, send failure details.
- **Benefit:** Consistent error UX and fewer duplicated dialog code paths.

### Fixed

#### Fixed: History Compatibility Regression in MainWindow
- **File:** `src/equinox/gui/window/_history.py`
- **Issue:** Tests and callers expecting `MainWindow._request_from_history` broke after mapper relocation.
- **Fix:** Restored compatibility wrapper delegating to `HistoryFacade.request_from_entry(...)`.
- **Benefit:** Preserves compatibility while keeping the new service boundary.

#### Fixed: Pytest Import File Mismatch for Duplicate Test Module Name
- **File:** `tests/application/history/test_history_facade.py`
- **Issue:** Duplicate `test_facade.py` basenames caused import mismatch in pytest collection.
- **Fix:** Renamed history test module to `test_history_facade.py`.
- **Benefit:** Stable test discovery with separate collection/history facade suites.

#### Fixed: Request Interpolation Test Patch Target Drift
- **File:** `tests/gui/test_request_interpolation_chain.py`
- **Issue:** Tests patched removed internals after send-flow service migration.
- **Fix:** Updated tests to patch `prepare_send`/`ErrorPresenter.warning` at current boundary.
- **Benefit:** Tests now verify behavior at the supported service boundary.

### Testing

#### Added: Application Facade Tests
- **Files:**
  - `tests/application/collections/test_facade.py`
  - `tests/application/history/test_history_facade.py`
  - `tests/gui/test_error_presenter.py`
- **Coverage:** collection operation routing, history facade mapping/search/stats behavior, and standardized error presentation.

#### Updated: Cross-Module Regression Slices
- **Files:** Multiple GUI/application test modules across request, collection, and history boundaries.
- **Result:** Refactor completed with targeted and broad regression runs during migration.

### For Upgrading

- No user-facing breaking changes.
- For internal extensions and custom tests:
  - Use `CollectionFacade` and `HistoryFacade` instead of direct storage manager access from GUI code.
  - Use `ErrorPresenter` for new GUI dialog paths to keep error UX consistent.
  - Avoid relying on removed GUI internals; prefer application-layer contracts.

---

**Total Changes (high-level):**
- Completed GUI service-boundary refactor.
- Added facades for collection/history flows and a centralized error presenter.
- Migrated send-flow error handling to unified presenter APIs.
- Stabilized compatibility edges discovered during broad regression testing.

