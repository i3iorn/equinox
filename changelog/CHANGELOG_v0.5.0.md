# Changelog — Equinox v0.5.0

**Release Date:** August 10, 2026

## Overview

Equinox v0.5.0 opens the 0.5.x line. It is substantially larger than any release in the 0.4.x series — 337 files touched against 18–70 for a typical 0.4.x release — and it changes how the project is installed, so it takes a minor version rather than continuing the 0.4.13 release candidate.

Three themes dominate. **Dependency management** was simplified: the committed lockfile is gone and `pyproject.toml` is now the sole source of truth. **The CI/CD pipeline** was reworked so `dev` is the gated integration branch and `master` is release-only, with mypy strict compliance enforced identically in CI and locally. **The GUI** received a broad audit that fixed security, data-loss, and stability defects, plus a layout pass that stopped the sidebar silently hiding navigation.

---

## [0.5.0] — 2026-08-10

### Dependency Management

#### Removed: Committed lockfile and `requirements.txt`
- **Files:** `requirements-lock.txt`, `requirements.txt`, `scripts/manage_requirements_lock.py`, `tests/scripts/test_manage_requirements_lock.py`, `.pre-commit-config.yaml`, `.github/workflows/ci.yml`
- **Change:** Removed the committed lockfile along with its generation script, pre-commit hook, CI step, and every documentation reference. `pyproject.toml` is now the only authoritative dependency manifest.
- **Benefit:** The lockfile could never be verified on Windows — `pip-compile` resolves differently per OS (a spurious `colorama` entry, truncated "via" comments), so the freshness check reported drift no matter what was committed and had to be skipped on every push. Removing it eliminates a gate that could not pass rather than papering over it.

#### Updated: Vulnerability scanning audits the installed environment
- **Files:** `scripts/check_dependency_vulnerabilities.py`, `tests/scripts/test_check_dependency_vulnerabilities.py`
- **Change:** `pip-audit` now scans the installed environment instead of a lockfile, using `--skip-editable` and no `--strict`.
- **Benefit:** Keeps a blocking CVE gate without a lockfile. `--strict` had to go because it treats *any* skipped package as fatal — including the always-editable local install — which made the scan exit before producing results at all.

#### Fixed: `setuptools` CVE surfaced by unpinned resolution
- **Files:** `pyproject.toml`
- **Change:** Pinned `setuptools>=83.0.0` in the `dev` extra, and raised the `[build-system]` floor to match.
- **Benefit:** Without lockfile pins, `setuptools` resolved to the interpreter's bundled `79.0.1`, which carries **PYSEC-2026-3447 / CVE-2026-59890** (a `MANIFEST.in` exclusion bypass on Unicode-normalizing filesystems). Declaring it in the manifest upgrades it through the normal install path everywhere, rather than duplicating an ad-hoc upgrade step across CI jobs.

#### Updated: Third-party dependency bumps
- **Files:** `pyproject.toml`
- **Change:** `urlps` 0.5.1 → 0.6.1 and `cryptography` 49.0.0 → 50.0.0.
- **Benefit:** Picks up upstream security and correctness fixes.

### URL Handling

#### Fixed: `urlps` integration had never actually run
- **Files:** `src/equinox/core/urls/parsing.py`
- **Change:** `_build_parser()` called `urlps.parse()`, a function that has never existed in any published version of `urlps`. The probe raised, the `except` swallowed it, and the parser silently fell back to `urllib.parse.urlparse` for the entire life of the dependency. Switched both call sites to the documented `urlps.parse_url_unsafe()`, with a per-call fallback to the stdlib splitter for input `urlps` refuses to parse (empty strings, `http://` with no host).
- **Benefit:** The declared dependency now does its job. The defensive fallback is retained deliberately: callers rely on this module always returning best-effort components, and `parse_url_unsafe()` raises where `urlparse()` does not. SSRF protection is unaffected — `_SsrfGuard` is enforced independently at send time by `_UrlValidator.validate_resolved()`.

### CI/CD & Quality Gates

#### Updated: Branch model — `dev` gated, `master` release-only
- **Files:** `.github/workflows/ci.yml`, `.github/workflows/security.yml`, `WORKFLOW.md`, `CONTRIBUTING.md`, `DEVELOPMENT.md`, `AGENTS.md`
- **Change:** Every push and PR into `dev` runs the full pipeline; `master` changes only through the `dev → master` promotion PR, with tags on `master` triggering the release workflow.
- **Benefit:** Gives a single gated integration point and keeps release history on `master` clean.

#### Fixed: Security workflow reported a red verdict for a passing scan
- **Files:** `.github/workflows/security.yml`
- **Change:** Added `pull-requests: write` / `issues: write` to the job permissions, and wrapped the PR-comment call in `try`/`catch` with `await`.
- **Benefit:** The comment step was failing with a 403 (`Resource not accessible by integration`) on every PR. The scans themselves passed, but the comment failure turned the whole security gate red — inverting the verdict and masking real results.

#### Fixed: mypy strict compliance and local/CI config drift
- **Files:** `pyproject.toml`, and ~32 modules across `src/equinox/`
- **Change:** Resolved all `mypy --strict` errors, moved `strict = true` into `[tool.mypy]` so the documented local command and CI's invocation agree by construction, and pinned `platform = "linux"` so platform-conditional stubs evaluate the same way on both.
- **Benefit:** "Passes locally" now genuinely means "passes in CI" — the two previously disagreed by 70 vs. 93 errors on the same tree.

#### Fixed: Python 3.10 compatibility and missing test dependency
- **Files:** `src/equinox/core/log_setup.py`, `pyproject.toml`, `.pre-commit-config.yaml`
- **Change:** Restored 3.10-valid syntax (pinning `pyupgrade` to `--py310-plus` to stop it regressing), and declared `pytest-qt` as a dev dependency.
- **Benefit:** The 3.10 matrix leg builds and the GUI suite runs from a clean install.

#### Fixed: Sandboxed script execution deadlocked under Qt
- **Files:** `src/equinox/core/scripts/`
- **Change:** Forced the `spawn` start method for sandboxed pre/post-request script execution.
- **Benefit:** Forking a process with an initialized Qt event loop could deadlock; `spawn` avoids inheriting that state.

### GUI

#### Fixed: Sidebar silently hid navigation and elided its toolbars
- **Files:** `src/equinox/gui/window/__init__.py`, `src/equinox/gui/window/_panels.py`, `src/equinox/gui/collection_panel/panel.py`, `src/equinox/gui/theme/stylesheet.qss`, `tests/gui/test_sidebar_layout_robustness.py`
- **Change:** Disabled tab scroll buttons and expansion in favour of `ElideRight`, shortened over-long toolbar labels with tooltips carrying the full wording, and re-applied tab tooltips after lazy panel initialization.
- **Benefit:** All six navigation destinations stay reachable at any sidebar width instead of disappearing behind scroll arrows, and toolbar labels no longer degrade into unreadable stubs. Lazy init swaps a tab via `removeTab`/`insertTab`, which had been discarding the tooltip at the exact moment the user first opened the tab.

#### Fixed: Variables panel toolbars clipped their own labels
- **Files:** `src/equinox/gui/variables_panel/_groups_mixin.py`, `src/equinox/gui/variables_panel/_session_vars_mixin.py`
- **Change:** Shortened the Groups/Variables and session-variable button labels (tooltips preserve the full text) and trimmed the panels' outer layout margins.
- **Benefit:** A two-column splitter sized for an 800px standalone panel gets squeezed into the ~300px sidebar tab, leaving buttons narrower than their text.

#### Fixed: GUI audit findings — security, data loss, and stability
- **Files:** `src/equinox/gui/workers.py`, `src/equinox/gui/dialogs/`, `src/equinox/gui/websocket_panel.py`, `src/equinox/gui/response_panel/`, and related panels
- **Change:** Redacted OAuth tokens in the token tester and removed a double-emitted signal; stopped `.env` import from dumping a secret dictionary into a message box; added dirty-state guards to the saved-credentials and environment dialogs; made variable rename atomic; fixed a WebSocket panel thread leak on close; and reverted theme/font changes when preferences are dismissed.
- **Benefit:** Closes the leak of credentials into logs and dialogs, and removes several paths that silently discarded unsaved user edits.

#### Fixed: Authentication configuration flows
- **Files:** `src/equinox/gui/dialogs/`, `src/equinox/gui/dialogs/_saved_credentials_auth_collector.py`, `tests/gui/test_auth_dialog_regressions.py`
- **Change:** Restored the auth configuration flows in `AuthDialog` and the credential manager, broke a circular import between `secret_manager_panel` and `dialogs`, and made `_validate_token_auth`'s type narrowing version-independent.
- **Benefit:** Auth configuration works again, and the module imports cleanly regardless of import order.

#### Added: `.env` import in the environment dialog
- **Files:** `src/equinox/gui/dialogs/environment_dialog/dotenv_importer.py`, `src/equinox/gui/dialogs/environment_dialog/__init__.py`
- **Change:** Added an "Import .env…" action to the environment dialog, which merges variables parsed from a `.env` file into the current environment.
- **Benefit:** Existing `.env` configuration can be pulled into an environment without retyping it.

### Correctness & Tests

#### Fixed: Deprecated `datetime.utcnow()` usage
- **Files:** `src/equinox/core/secret_managers/base.py`, `tests/core/test_secret_managers_coverage.py`
- **Change:** Replaced the remaining `datetime.utcnow()` calls with `datetime.now(timezone.utc).replace(tzinfo=None)`, matching the convention used elsewhere.
- **Benefit:** Clears the last `DeprecationWarning` from the suite ahead of the function's removal.

#### Fixed: Test suite made a live network call
- **Files:** `tests/core/test_auth_header_preservation.py`
- **Change:** Replaced a real request to `httpbin.org` with `httpx.MockTransport` injected at the dispatcher.
- **Benefit:** The test verified Equinox's own header-redaction logic and never checked the response body, yet depended on an external service — making it flaky under full-suite load and failing without network access.

### Refactoring

#### Updated: Package structure and size policy
- **Files:** `src/equinox/gui/request_panel/_mixins/auth_mixin/`, `src/equinox/gui/request_panel/_mixins/body_mixin/`, `scripts/check_code_component_size.py`
- **Change:** Promoted the larger request-panel mixins to packages, grouped related mixins into modules, removed dead code (an orphaned context-menu mixin, unused dotenv helpers, duplicated header presets), and corrected the code-size thresholds to match documented policy.
- **Benefit:** Keeps modules within the project's own size limits and removes drift between the checker and the policy it enforces.

#### Updated: Repo-wide formatting pass
- **Files:** repo-wide
- **Change:** Applied `ruff format`, `pyupgrade`, and `add-trailing-comma`, and pinned the ruff pre-commit hook to the version CI resolves.
- **Benefit:** Clears a backlog of format drift that had accumulated silently and prevents it recurring.

---

## Upgrade Notes

- **Installation changed.** `requirements.txt` and `requirements-lock.txt` no longer exist. Install with `pip install -e .` (or `pip install -e ".[dev]"` for development); `pyproject.toml` is the sole dependency manifest. Any tooling that referenced those files needs updating.
- **Existing environments should reinstall** (`pip install -e ".[dev]"`) to pick up the patched `setuptools`, otherwise the blocking dependency scan will fail locally.
- No database migrations and no changes to stored data or the public Python API.
