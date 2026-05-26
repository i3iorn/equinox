"""100% coverage tests for equinox.core.client.__init__

Covers:
- Public re-export of HTTPClient
- __all__ contract
- Package-level attribute accessibility
- Identity between package export and concrete module class
- Sad-path import guards
"""

from __future__ import annotations

import importlib
import sys
import types

import pytest

# ---------------------------------------------------------------------------
# Happy path — import surface
# ---------------------------------------------------------------------------


class TestHTTPClientImport:
    """HTTPClient is importable from the package's public API."""

    def test_http_client_importable_from_package(self) -> None:
        from equinox.core.client import HTTPClient  # noqa: F401 — import must not raise

        assert HTTPClient is not None

    def test_http_client_is_a_class(self) -> None:
        from equinox.core.client import HTTPClient

        assert isinstance(HTTPClient, type)

    def test_http_client_class_name(self) -> None:
        from equinox.core.client import HTTPClient

        assert HTTPClient.__name__ == "HTTPClient"

    def test_package_attribute_http_client(self) -> None:
        import equinox.core.client as pkg

        assert hasattr(pkg, "HTTPClient")

    def test_http_client_is_same_object_as_concrete_module(self) -> None:
        """Package re-export must be the identical class, not a copy."""
        from equinox.core.client import HTTPClient as pkg_cls
        from equinox.core.client.http_client import HTTPClient as concrete_cls

        assert pkg_cls is concrete_cls


# ---------------------------------------------------------------------------
# Happy path — __all__ contract
# ---------------------------------------------------------------------------


class TestDunderAll:
    """__all__ must be present, correct, and minimal."""

    def test_all_is_defined(self) -> None:
        import equinox.core.client as pkg

        assert hasattr(pkg, "__all__")

    def test_all_contains_http_client(self) -> None:
        import equinox.core.client as pkg

        assert "HTTPClient" in pkg.__all__

    def test_all_is_exactly_one_entry(self) -> None:
        import equinox.core.client as pkg

        assert list(pkg.__all__) == ["HTTPClient"]

    def test_all_is_a_list(self) -> None:
        import equinox.core.client as pkg

        assert isinstance(pkg.__all__, list)

    def test_all_entries_are_strings(self) -> None:
        import equinox.core.client as pkg

        assert all(isinstance(name, str) for name in pkg.__all__)


# ---------------------------------------------------------------------------
# Happy path — module metadata
# ---------------------------------------------------------------------------


class TestModuleMetadata:
    """Package __init__ should carry the expected module-level dunder attributes."""

    def test_package_has_docstring(self) -> None:
        import equinox.core.client as pkg

        assert pkg.__doc__ is not None
        assert len(pkg.__doc__.strip()) > 0

    def test_docstring_mentions_http_client(self) -> None:
        import equinox.core.client as pkg

        assert "HTTPClient" in pkg.__doc__

    def test_package_is_a_module(self) -> None:
        import equinox.core.client as pkg

        assert isinstance(pkg, types.ModuleType)

    def test_package_name(self) -> None:
        import equinox.core.client as pkg

        assert pkg.__name__ == "equinox.core.client"


# ---------------------------------------------------------------------------
# Happy path — re-import idempotency
# ---------------------------------------------------------------------------


class TestReImportIdempotency:
    """Importing the package multiple times must yield the same objects."""

    def test_repeated_import_returns_same_class(self) -> None:
        from equinox.core.client import HTTPClient as first
        from equinox.core.client import HTTPClient as second  # noqa: PLC0415

        assert first is second

    def test_module_cached_in_sys_modules(self) -> None:
        import equinox.core.client  # noqa: F401

        assert "equinox.core.client" in sys.modules

    def test_reload_preserves_http_client_identity_with_concrete_module(self) -> None:
        """After a manual reload the re-export must still point to the concrete class."""
        import equinox.core.client as pkg

        importlib.reload(pkg)

        from equinox.core.client.http_client import HTTPClient as concrete_cls

        assert pkg.HTTPClient is concrete_cls


# ---------------------------------------------------------------------------
# Sad path — importing non-existent names
# ---------------------------------------------------------------------------


class TestImportErrors:
    """Names not in __all__ and not defined in the package must raise ImportError."""

    def test_import_nonexistent_name_raises(self) -> None:
        with pytest.raises(ImportError):
            from equinox.core.client import (
                NonExistentClass,  # type: ignore[attr-defined]  # noqa: F401
            )

    def test_getattr_nonexistent_raises_attribute_error(self) -> None:
        import equinox.core.client as pkg

        with pytest.raises(AttributeError):
            _ = pkg.NonExistentName  # type: ignore[attr-defined]

    def test_all_entries_are_resolvable(self) -> None:
        """Every name declared in __all__ must actually exist on the package."""
        import equinox.core.client as pkg

        for name in pkg.__all__:
            assert hasattr(pkg, name), f"{name!r} is in __all__ but missing from the package"

    def test_http_dispatcher_not_in_public_api(self) -> None:
        """Internal classes must not be promoted to the public namespace."""
        import equinox.core.client as pkg

        assert "HttpxDispatcher" not in pkg.__all__

    def test_request_pipeline_not_in_public_api(self) -> None:
        import equinox.core.client as pkg

        assert "RequestPipeline" not in pkg.__all__
