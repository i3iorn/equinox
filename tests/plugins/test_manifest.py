"""Tests for PluginManifest parsing, validation, and round-trip serialization."""

from __future__ import annotations

import pytest

from equinox.core.exceptions import SecurityError
from equinox.plugins.security import Permission, PluginManifest


class TestPluginManifestFromDict:
    def test_minimal_valid_manifest(self) -> None:
        data = {"name": "my-plugin", "version": "1.0.0", "author": "tester"}
        m = PluginManifest.from_dict(data)
        assert m.name == "my-plugin"
        assert m.version == "1.0.0"
        assert m.author == "tester"
        assert m.description == ""
        assert m.permissions == set()
        assert m.checksum is None

    def test_full_manifest(self) -> None:
        data = {
            "name": "full",
            "version": "2.0.0",
            "author": "a",
            "description": "desc",
            "permissions": ["network.http", "file.read"],
            "homepage": "https://example.com",
            "license": "MIT",
            "checksum": "abc123",
        }
        m = PluginManifest.from_dict(data)
        assert m.permissions == {Permission.NETWORK_HTTP, Permission.FILE_READ}
        assert m.homepage == "https://example.com"
        assert m.license == "MIT"
        assert m.checksum == "abc123"

    def test_non_dict_raises(self) -> None:
        with pytest.raises(SecurityError, match="must be a JSON object"):
            PluginManifest.from_dict("not a dict")  # type: ignore[arg-type]

    def test_missing_name_raises(self) -> None:
        with pytest.raises(KeyError):
            PluginManifest.from_dict({"version": "1.0.0", "author": "a"})

    def test_permissions_not_list_raises(self) -> None:
        data = {
            "name": "x",
            "version": "1.0",
            "author": "a",
            "permissions": "network.http",  # type: ignore[typeddict-item]
        }
        with pytest.raises(SecurityError, match="must be a list"):
            PluginManifest.from_dict(data)

    def test_non_string_permission_raises(self) -> None:
        data = {
            "name": "x",
            "version": "1.0",
            "author": "a",
            "permissions": [123],  # type: ignore[list-item]
        }
        with pytest.raises(SecurityError, match="must be strings"):
            PluginManifest.from_dict(data)

    def test_unknown_permission_raises(self) -> None:
        data = {
            "name": "x",
            "version": "1.0",
            "author": "a",
            "permissions": ["nonexistent.perm"],
        }
        with pytest.raises(SecurityError, match="Unknown plugin permission"):
            PluginManifest.from_dict(data)

    def test_all_known_permissions_parse(self) -> None:
        perm_values = [p.value for p in Permission]
        data = {
            "name": "x",
            "version": "1.0",
            "author": "a",
            "permissions": perm_values,
        }
        m = PluginManifest.from_dict(data)
        assert m.permissions == set(Permission)


class TestPluginManifestToDict:
    def test_round_trip(self) -> None:
        data = {
            "name": "rt",
            "version": "0.1",
            "author": "t",
            "description": "d",
            "permissions": ["network.https", "storage.read"],
            "homepage": "https://x.com",
            "license": "BSD",
            "checksum": "sha",
        }
        m = PluginManifest.from_dict(data)
        out = m.to_dict()
        assert out["name"] == "rt"
        assert set(out["permissions"]) == {"network.https", "storage.read"}
        assert out["checksum"] == "sha"

    def test_to_dict_returns_new_dict(self) -> None:
        m = PluginManifest(name="a", version="1", author="b")
        d1 = m.to_dict()
        d1["name"] = "mutated"
        assert m.name == "a"
