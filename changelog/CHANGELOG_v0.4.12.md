# Changelog: `chore/request_panel_refactoring`

Comparison base: `master`

## Overview
This branch delivers a broad refactor of request/response panel and related workflow components, focused on error handling, logging quality, type-hint consistency, and UI wiring cleanup.

- Commits: 17
- Diff size: 54 files changed, 887 insertions, 907 deletions

## Highlights
- Refactored request panel assertions flow by introducing a dedicated assertions-tab creation method and aligning tests.
- Improved robustness across request panel components with stronger error handling and logging.
- Expanded type-hint coverage and documentation in mixins and related modules.
- Improved response-side error reporting (including `pretty_print`) using exception-level logging where appropriate.
- Updated body type bar margin behavior in request panel layout.

## Security and Privacy
- Added or expanded sensitive URL/data redaction in logs for:
  - validation paths
  - send mixin logging
  - cURL import logging
  - autosave logging
- Adjusted auth/status logging behavior and severity to reduce sensitive signal leakage.

## Refactoring Details
- **Request Panel**
  - Initialization and panel wiring updates with safer `QWidget` casting and improved exception handling.
  - Assertions mixin cleanup and supporting documentation/type updates.
- **Response Panel**
  - Formatting/display/panel error-path logging refinements.
- **History and Intelligence**
  - Stronger typing and exception handling in history search/serialization/manager paths and intelligence worker methods.
- **Facade and Importers**
  - Facade methods and Postman importer paths updated for consistent return typing and clearer failure handling.
- **Auth**
  - Authentication components and `.pyi` stubs updated for type and error-handling consistency.
- **Window/UI wiring**
  - Request/history/intelligence integration paths refined for reliability and observability.

## Testing Notes
- Tests were updated alongside assertions-tab refactoring.
- Changes are primarily internal quality, safety, and maintainability improvements.
- Recommended validation focus:
  - request send/auth flows
  - assertions tab lifecycle
  - history/intelligence interactions
  - logging output and redaction behavior

## File Impact Summary
High-impact areas include:
- `src/equinox/gui/request_panel/*`
- `src/equinox/gui/response_panel/*`
- `src/equinox/storage/history/*`
- `src/equinox/importers/postman.py`
- `src/equinox/gui/intelligence_worker.py`
- `src/equinox/gui/window/*`
- `src/equinox/auth/*.pyi`

This branch is a maintenance/refactor update emphasizing safer logging, stronger privacy redaction, improved typing, and cleaner panel architecture.
