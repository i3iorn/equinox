# Changelog — Equinox v0.7.3

**Release Date:** October 27, 2026

## Overview

Equinox v0.7.3 is a UX-focused release that makes the tool friendlier to new
users and smoother for daily use. It adds a guided empty-state onboarding card,
non-blocking toast notifications, a density toggle, history favorites, a native
title-bar option on Windows, and global keyboard shortcuts that work from inside
text fields.

No breaking changes: existing requests, collections, credentials, and history
databases load and save exactly as before. No database migrations are required.

---

## [0.7.3] — 2026-10-27

### Improvements

#### Empty-state onboarding card (#100)
- When the request editor is blank, a guided card now appears with a "Create
  sample request" button that loads `https://httpbin.org/get` and focuses the
  URL field, so first-time users are never staring at an empty editor.
- The card hides automatically once a URL is entered or a request is loaded.
- **Files:** `src/equinox/gui/request_panel/_mixins/panel_layout_mixin.py`,
  `src/equinox/gui/request_panel/_mixins/body_mixin/request_loading_mixin.py`

#### Toast notification system (#101)
- New non-blocking toast banners (success/warning/error/info) appear above the
  request/response area and auto-dismiss, replacing interruptive modal dialogs
  for transient feedback such as "Saved", "200 OK", and "Request cancelled".
- **Files:** `src/equinox/gui/widgets/toast.py` (new),
  `src/equinox/gui/window/__init__.py`,
  `src/equinox/gui/theme/stylesheet.qss`

#### Enhanced send-button feedback (#102)
- The Send button now reads "Sending…" while a request is in flight and shows
  elapsed time ("Sending… 1.2s"), with a visible Cancel button and a cancel
  toast. Previously it only showed a raw timer.
- **Files:** `src/equinox/gui/request_panel/_mixins/send_mixin/send_worker_mixin.py`

#### UI density toggle (#103)
- New Preferences → Appearance → UI density setting: Compact, Comfortable
  (default), or Spacious. It scales button/input padding and font size across
  the whole application and is persisted in QSettings.
- **Files:** `src/equinox/gui/theme/settings.py`,
  `src/equinox/gui/theme/manager.py`,
  `src/equinox/gui/theme/__init__.py`,
  `src/equinox/gui/theme/stylesheet.py`,
  `src/equinox/gui/theme/stylesheet.qss`,
  `src/equinox/gui/dialogs/preferences_dialog.py`

#### Splitter handle visibility (#104)
- The request/response and main splitters now show a visible, grab-handle-sized
  (6px) bar with hover/pressed states instead of a nearly-invisible 1px line,
  making panels easier to resize on high-DPI displays.
- **Files:** `src/equinox/gui/theme/stylesheet.qss`

#### History favorites (#105)
- History entries can be starred/unstarred from the list (★/☆) and context
  menu. Favorites sort to the top of the history list and a "Favorites only"
  filter checkbox narrows the view. Selections are persisted in QSettings.
- **Files:** `src/equinox/gui/history_panel.py`

#### Native title-bar option (#106)
- New Preferences → Appearance → "Use native OS title bar" option. On Windows
  this gives standard window controls, taskbar previews, and snap support;
  frameless remains the default. Requires a restart to take effect.
- **Files:** `src/equinox/gui/theme/settings.py`,
  `src/equinox/gui/theme/__init__.py`,
  `src/equinox/gui/window/__init__.py`,
  `src/equinox/gui/dialogs/preferences_dialog.py`

#### Global keyboard shortcuts (#107)
- `Alt+1`–`Alt+6` sidebar shortcuts now use `Qt.ApplicationShortcut`, so they
  fire regardless of which widget has focus (e.g. inside the URL or body
  editors). Editors that handle a shortcut themselves keep their own behavior.
- **Files:** `src/equinox/gui/window/__init__.py`

### Bug Fixes / Core

- **Stylesheet cache key**: The per-theme stylesheet cache now includes density
  and font size so switching density or zoom regenerates the correct stylesheet
  instead of reusing a stale one.
- **Preference cancellation**: Cancelling Preferences now restores the original
  UI density and native-title-bar choice along with font size and theme.
- **Request panel imports**: `DENSITY_COMFORTABLE`, `DENSITY_LABELS`, and
  `DENSITY_MODES` are exported from `equinox.gui.theme` so existing call sites
  keep working.

### Upgrade Notes

- **No breaking changes.** Requests, collections, saved credentials, history,
  and cookies load and save unchanged; no database migrations and no changes to
  stored data.
- New settings (density, native title bar, favorited history IDs) default to
  Comfortable, frameless, and empty respectively.

---

## Compatibility

- No API or data-format changes. Existing collections, credentials, history
  databases, and stored cookies load and save exactly as before.
- Minimum Python version remains 3.10; PyQt6>=6.5.0,<7.0.0.
