# Changelog — Equinox v0.7.0

**Release Date:** October 19, 2026

## Overview

Equinox v0.7.0 adds configurable OAuth2 token-endpoint support. Token services
that deviate from the RFC 6749 response shape — such as D&B Risk Analytics,
which returns its token in a `JWTToken` field, expects a JSON token request
body, and requires an extra `Authorization: Bearer <api-key>` header on the
token call itself — now work without a separate manual token-exchange call.

All new configuration is optional and defaults to the previous behavior, so
existing OAuth2 setups are unaffected.

---

## [0.7.0] — 2026-10-19

### New Features

#### Added: Configurable OAuth2 token-endpoint behavior (JWT support)
- **Files:** `src/equinox/auth/_oauth2/oauth2_auth.py`, `src/equinox/auth/_oauth2/operations.py`, `src/equinox/gui/dialogs/auth_dialog/tabs/oauth2.py`, `tests/auth/test_oauth2_token_config.py`
- **Change:** `OAuth2Auth` gained four configuration fields: `token_field_name` (the response JSON key holding the token, default `access_token`), `token_body_format` (`form` default or `json` encoding for the token POST), `token_request_headers` (extra headers merged onto the token request; custom values override the RFC 6749 Basic header when both are set), and `default_token_ttl_seconds` (fallback expiry when the response omits `expires_in`). The OAuth2 auth dialog exposes the three user-facing fields, and `{{VAR}}` placeholders in header values are interpolated at use time.
- **Benefit:** Token services that deviate from the standard shape work out of the box — e.g. D&B Risk Analytics returns its JWT in a `JWTToken` field, expects a JSON body, and requires an `Authorization: Bearer <api-key>` header on the token call itself. The client now acquires and caches the token automatically on first request instead of forcing a separate manual exchange.

---

## Upgrade Notes

- **No breaking changes.** All four new `OAuth2Auth` fields are optional with backward-compatible defaults (`access_token` field name, form-encoded body, no extra headers, 3600s fallback TTL). Existing stored OAuth2 configurations deserialize unchanged and behave exactly as before.
- No database migrations and no changes to stored data.
