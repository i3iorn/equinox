# Changelog — Equinox v0.8.2

**Release Date:** November 3, 2026

## Overview

Patch release for v0.8.1. Closes two silent exception-swallow gaps that were
making runtime failures invisible to users and logs.

---

## [0.8.2] — 2026-11-03

### Bug Fixes

- **GUI app now logs API-spec-dialog failures**: The dialog-open handler
  previously caught ``Exception`` with a bare ``pass`` and a comment
  ("exception is already logged above") — the exception was *not* logged,
  so a dialog failure vanished silently. It now emits
  ``logger.exception("Failed to open API spec dialog: %s", exc_info=True)``.
- **Files:** `src/equinox/gui/app.py`

- **Postman importer now logs failed collection-variable adds**: In the
  non-transactional fallback path, a failed ``add_variable`` call was
  swallowed with ``pass``. It now logs the failure so a partially-imported
  Postman collection's missing variables are diagnosable.
- **Files:** `src/equinox/importers/postman.py`

### Upgrade Notes

- No data or configuration changes. Behavior is identical to v0.8.1 except
  for the two newly-logged error paths.
