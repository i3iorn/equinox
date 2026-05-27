"""Compatibility wrapper for request-panel body helpers.

All non-UI request body assembly and detection logic now lives in the
application layer. This module remains only to preserve import compatibility
for existing GUI code and tests.
"""
from equinox.application.requests import detect_body_type
from equinox.application.requests import interpolate_auth
from equinox.application.requests._assembly import assemble_body
from equinox.application.requests._assembly import inject_content_type

__all__ = [
    "assemble_body",
    "inject_content_type",
    "detect_body_type",
    "interpolate_auth",
]
