from equinox.versioning import get_app_version


def test_get_app_version_returns_non_empty_string() -> None:
    assert isinstance(get_app_version(), str)
    assert get_app_version().strip()
