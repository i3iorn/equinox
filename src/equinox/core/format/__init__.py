"""Formatting and error handling utilities.

This package contains utilities for structured logging, error enrichment, and
error mapping for improved error visibility and debugging.
"""

from equinox.core.format.error_enrichment import RichError, enrich_exception
from equinox.core.format.error_mapper import build_error_handlers

__all__ = [
    "enrich_exception",
    "RichError",
    "build_error_handlers",
]
