"""URL handling facade.

This package split keeps the public import path stable:
`from equinox.core import urls` and `from equinox.core.urls import ...`.
"""

from typing import Any

from equinox.core.interpolation import VariableInterpolator

from . import normalizer as _normalizer
from . import parsing as _parser
from .utils import append_query_params, join_url_path

_URLComponents = _parser.URLComponents
_parse_url = _parser._parse_url
_normalize_segment = _normalizer._normalize_segment


def _sync_parser_aliases() -> None:
    """Keep parser aliases patchable at package root for compatibility tests."""
    _parser._parse_url = _parse_url
    _normalizer._parse_url = _parse_url


def expand_placeholders(url: str, variables: dict[str, str] | None = None) -> str:
    _sync_parser_aliases()
    return _normalizer.expand_placeholders(url, variables)


def normalized_parts(url: str, variables: dict[str, str] | None = None) -> dict[str, Any]:
    _sync_parser_aliases()
    return _normalizer.normalized_parts(url, variables)


def normalize_url(url: str, variables: dict[str, str] | None = None) -> str:
    _sync_parser_aliases()
    return _normalizer.normalize_url(url, variables)


def base_path(normalized_url: str) -> str:
    _sync_parser_aliases()
    return _normalizer.base_path(normalized_url)


def url_metadata(url: str) -> dict[str, Any]:
    _sync_parser_aliases()
    return _parser.url_metadata(url)


def parse_query_pairs(query: str, keep_blank_values: bool = True):
    return _parser.parse_query_pairs(query, keep_blank_values=keep_blank_values)


__all__ = [
    "expand_placeholders",
    "normalized_parts",
    "normalize_url",
    "base_path",
    "url_metadata",
    "parse_query_pairs",
    "append_query_params",
    "join_url_path",
    "_URLComponents",
    "_parse_url",
    "_normalize_segment",
    "VariableInterpolator",
]
