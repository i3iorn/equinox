# Changelog — Equinox v0.7.1

**Release Date:** October 23, 2026

## Overview

Equinox v0.7.1 unifies the two authentication-configuration surfaces so they
share the same underlying GUI code. The request-builder "Configure
Authentication" dialog and the "Manage Saved Credentials" dialog previously
maintained separate, hand-built form implementations for Basic, Bearer,
OAuth 2.0, API Key, and AWS SigV4 — editing one had to be manually mirrored in
the other. Both now embed the same shared tab widgets, guaranteeing identical
field sets, labels, validation, and behavior everywhere.

This is a refactor with no behavioral breaking changes: existing requests,
collections, and saved credentials load and save exactly as before.

---

## [0.7.1] — 2026-10-23

### Improvements

#### Unified authentication configuration UI (single source of truth)
- **Files:** `src/equinox/gui/dialogs/auth_dialog/tabs/*`, `src/equinox/gui/dialogs/auth_dialog/__init__.py`, `src/equinox/gui/dialogs/saved_credentials_dialog.py`, `src/equinox/gui/dialogs/_saved_credentials_auth_collector.py`
- **Change:** The per-type form widgets in `gui/dialogs/auth_dialog/tabs/`
  (Basic, Bearer, OAuth 2.0, API Key, AWS SigV4) now serve both hosts. The
  Saved Credentials dialog embeds the shared tabs instead of its own hand-built
  pages, and both required-field validation and config population route through
  the shared tab contract (`validate()`, `set_auth_config()`, `get_auth_config()`).
  - Field labels and placeholder text are now consistent across both dialogs
    (e.g. API Key "Key Name" instead of the previously divergent "Header/Param Name").
  - Required-field validation is identical in both windows — an incomplete form
    is rejected with the same message regardless of entry point.
- **Change:** The Saved Credentials OAuth2 form now exposes the full field set
  shared with the request dialog (grant type, extra parameters, token field
  name, custom token-request headers, TTL fallback, verify-SSL), removing the
  previous divergence where each dialog had fields the other lacked.
- **Change:** The request editor's "Fetch Token…" honors all shared OAuth2
  fields: extra params, token field name, custom headers, and TTL are passed to
  the strategy. Grant types that require interactivity (password,
  authorization code) show a clear status message instead of failing silently.

### Upgrade Notes

- **No breaking changes.** Requests, collections, and saved credentials load
  and save unchanged; existing stored OAuth2 configurations deserialize
  exactly as before. No database migrations and no changes to stored data.
