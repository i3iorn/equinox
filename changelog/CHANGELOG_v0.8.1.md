# Changelog — Equinox v0.8.1

**Release Date:** November 3, 2026

## Overview

Patch release for v0.8.0. Adds a fail-fast guard for stale urlps installs.

---

## [0.8.1] — 2026-11-03

### Bug Fixes

- **Actionable error for stale urlps installs**: Importing the URL validator
  now raises a clear ``ImportError`` ("Equinox requires urlps>=2.1.0 but
  installed urlps is X.Y.Z. Upgrade with: pip install --upgrade
  'urlps>=2.1.0'") when an environment still carries an older urlps. Previously
  affected users saw a confusing ``TypeError:
  SecurityPolicy.__init__() got an unexpected keyword argument
  'flag_injection_payloads'`` deep inside ``dataclasses.replace``.
- **Files:** `src/equinox/core/validation/_url.py`

### Upgrade Notes

- If you hit the ``TypeError`` above, either upgrade urlps directly
  (``pip install --upgrade 'urlps>=2.1.0'``) or install this patch release
  fresh — no data or configuration changes are involved.
- No other changes; identical to v0.8.0 in behavior when urlps >= 2.1.0 is
  installed.
