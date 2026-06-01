# Changelog — Equinox v0.4.9

**Release Date:** June 1, 2026

## Overview

Equinox v0.4.9 continues the 0.4.x line with a focused round of JSON tooling centralization, GUI layout cleanup, typing refinements, and dependency maintenance. The release keeps the codebase easier to reason about while preserving the security-first defaults and coverage gates added in the previous release.

---

## [0.4.9] — 2026-06-01

### JSON Tooling & Syntax Highlighting

#### Added: Centralized JSON tooling package
- **Files:** `src/equinox/core/json_tools/__init__.py`, `src/equinox/core/json_tools/decoder.py`, `src/equinox/core/json_tools/formatter.py`, `src/equinox/core/json_tools/lexer.py`, `src/equinox/core/json_tools/models.py`, `src/equinox/core/json_tools/traversal.py`, `src/equinox/core/json_tools/utils.py`, `src/equinox/core/json_tools/validation.py`
- **Change:** Consolidated the JSON parsing, traversal, formatting, and validation helpers behind a dedicated package boundary.
- **Benefit:** Makes JSON handling easier to reuse across the GUI, codegen, and test suites.

#### Added: Dedicated JSON syntax-highlighter subpackage
- **Files:** `src/equinox/gui/syntax_highlighter/json_highlighter/__init__.py`, `src/equinox/gui/syntax_highlighter/json_highlighter/formats.py`, `src/equinox/gui/syntax_highlighter/json_highlighter/highlighter.py`, `src/equinox/gui/syntax_highlighter/json_highlighter/lexer/`
- **Change:** Moved the JSON highlighter implementation into its own subpackage while keeping the public package export stable.
- **Benefit:** Separates tokenizer/highlighter concerns from the rest of the syntax-highlighting package and improves internal organization.

### GUI Layout & Panel Cleanup

#### Updated: Request and response panel organization
- **Files:** `src/equinox/gui/request_panel/_mixins/panel_layout_mixin.py`, `src/equinox/gui/request_panel/panel.py`, `src/equinox/gui/response_panel/actions_mixin.py`, `src/equinox/gui/response_panel/panel.py`, `src/equinox/gui/secret_manager_panel.py`
- **Change:** Refined panel layout helpers and action wiring to keep toolbar and menu behavior consistent.
- **Benefit:** Produces a cleaner, more predictable GUI structure with less layout-specific noise in the panel code.

#### Updated: Class size guardrails and supporting UI behavior
- **Files:** `scripts/check_code_component_size.py`, `src/equinox/core/exceptions.py`, `src/equinox/core/client/dispatcher.py`
- **Change:** Adjusted the size-checking utility and made small maintenance-oriented cleanups in supporting code paths.
- **Benefit:** Keeps the project’s maintainability checks aligned with current module shape and typing expectations.

### Typing, Tests & Quality

#### Improved: Type hints and test assertions
- **Files:** `src/equinox/core/client/dispatcher.py`, `src/equinox/core/codegen/python.py`, `src/equinox/gui/request_panel/panel.py`, `tests/core/test_module_size_monitoring.py`, `tests/gui/test_gui_widgets_coverage.py`, `tests/gui/test_json_lexer.py`, `tests/gui/test_ui_usage_tracker.py`
- **Change:** Tightened local type hints, simplified a few callable signatures, and updated assertions where UI behavior changed.
- **Benefit:** Reduces static-analysis noise and keeps the test suite aligned with the refactors.

#### Improved: Coverage around UI ranking and JSON parsing paths
- **Files:** `tests/gui/test_ui_usage_tracker.py`, `tests/gui/test_json_lexer.py`
- **Change:** Expanded regression coverage for ranked UI actions and JSON lexing behavior.
- **Benefit:** Protects the UI ordering and JSON-tooling refactors from accidental regressions.

### Dependency Maintenance

#### Updated: Requirements lockfile refresh
- **Files:** `requirements-lock.txt`, `.secrets.baseline`
- **Change:** Refreshed the dependency lockfile and security baseline to match the current environment.
- **Benefit:** Keeps the repository’s install and audit state in sync with the latest dependency set.

---

**Total Changes (high-level):**
- Centralized JSON tooling into a dedicated package.
- Moved JSON highlighting into its own subpackage while keeping the public export stable.
- Cleaned up request/response panel layout and surrounding UI wiring.
- Tightened typing and expanded coverage for JSON and UI ranking paths.
- Refreshed dependency and security maintenance artifacts.
