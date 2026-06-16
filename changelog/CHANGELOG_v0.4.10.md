# Changelog — Equinox v0.4.9

**Release Date:** June 16, 2026

## Overview

Equinox v0.4.10 is a minor update to fix the searchbar of the response panel to automatically scroll to the search matches. This is a quality-of-life improvement that makes it easier to navigate large responses when using the search functionality.

---

## [0.4.10] — 2026-06-16

### GUI Search Functionality

#### Added: Automatic scrolling to search matches in response panel searchbar
- **Files:** `src/equinox/gui/response_panel/search/ui.py`, `src/equinox/__init__.py`, `tests/gui/test_response_panel_improvements.py`,
- **Change:** Added automatic scrolling to search matches in the response panel's searchbar implementation.
- **Benefit:** Improves the user experience when searching through large responses by ensuring that matches are brought into view automatically.
