import pytest

from equinox.core import urls
from equinox.core.exceptions import ValidationError
from equinox.core.validation import Validator


def test_validate_resolved_url_rejects_invalid_scheme():
    with pytest.raises(ValidationError):
        Validator.validate_resolved_url("ftp://example.com/")


def test_validate_resolved_url_rejects_missing_host():
    # Missing netloc (e.g. http:///nohost)
    with pytest.raises(ValidationError):
        Validator.validate_resolved_url("http:///nohost")


def test_validate_resolved_url_accepts_expanded_url():
    expanded = urls.expand_placeholders("https://example.com/users/{{id}}", {"id": "1"})
    # Should not raise
    Validator.validate_resolved_url(expanded)
