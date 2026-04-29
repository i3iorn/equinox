"""Security-aware secret integration entrypoints (wrapper).

This module delegates to the existing storage.secret_integration module while
providing a security-focused import path.
"""

from __future__ import annotations

from equinox.storage.secret_integration import *  # noqa: F401,F403
