"""Tests for core/secret_managers — registry, env, base, connection."""

from __future__ import annotations

import pytest

from equinox.core.secret_managers.base import (
    SecretCacheEntry,
    SecretManagerError,
    SecretNotFoundError,
    _safe_secret_ref,
)
from equinox.core.secret_managers.env import EnvironmentVariableManager
from equinox.core.secret_managers.registry import (
    get_secret_manager,
    list_available_managers,
    register_manager,
)

# ── _safe_secret_ref ─────────────────────────────────────────────────────────


def test_safe_secret_ref_masks_long_name() -> None:
    ref = _safe_secret_ref("my_database_password")
    assert "my_d" in ref


# ── SecretCacheEntry ──────────────────────────────────────────────────────────


class TestSecretCacheEntry:
    def test_not_expired_immediately(self) -> None:
        entry = SecretCacheEntry("val", ttl_seconds=300)
        assert not entry.is_expired()

    def test_expired_after_zero_ttl(self) -> None:
        from datetime import datetime, timedelta, timezone

        entry = SecretCacheEntry("val", ttl_seconds=0)
        # Force retrieved_at to old time
        entry.retrieved_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1)
        assert entry.is_expired()


# ── SecretManager (via EnvironmentVariableManager as concrete subclass) ──────


class TestEnvironmentVariableManager:
    def test_is_available_always_true(self) -> None:
        mgr = EnvironmentVariableManager()
        assert mgr.is_available()

    def test_configure_sets_prefix(self) -> None:
        mgr = EnvironmentVariableManager()
        mgr.configure(prefix="CUSTOM_")
        assert mgr.prefix == "CUSTOM_"

    def test_configure_no_prefix_uses_default(self) -> None:
        mgr = EnvironmentVariableManager()
        mgr.configure()
        assert mgr.prefix == "EQUINOX_SECRET_"

    def test_get_secret_returns_env_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EQUINOX_SECRET_MY_KEY", "my_value")
        mgr = EnvironmentVariableManager()
        mgr.configure()
        assert mgr.get_secret("MY_KEY") == "my_value"

    def test_get_secret_not_found_raises(self) -> None:
        mgr = EnvironmentVariableManager()
        mgr.configure()
        with pytest.raises(SecretNotFoundError):
            mgr.get_secret("DEFINITELY_MISSING_XYZ_UNIQUE_99999")

    def test_get_secret_caches_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EQUINOX_SECRET_CACHED_KEY", "cached_val")
        mgr = EnvironmentVariableManager()
        mgr.configure()
        val1 = mgr.get_secret("CACHED_KEY")
        # Remove from env — next call should still return cached
        monkeypatch.delenv("EQUINOX_SECRET_CACHED_KEY")
        val2 = mgr.get_secret("CACHED_KEY")
        assert val1 == val2 == "cached_val"

    def test_get_secret_dict_valid_json(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EQUINOX_SECRET_JSON_KEY", '{"a": 1}')
        mgr = EnvironmentVariableManager()
        mgr.configure()
        result = mgr.get_secret_dict("JSON_KEY")
        assert result == {"a": 1}

    def test_get_secret_dict_invalid_json_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EQUINOX_SECRET_BAD_JSON", "not-json")
        mgr = EnvironmentVariableManager()
        mgr.configure()
        with pytest.raises(SecretManagerError, match="not valid JSON"):
            mgr.get_secret_dict("BAD_JSON")

    def test_clear_cache_specific_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EQUINOX_SECRET_CLR", "v")
        mgr = EnvironmentVariableManager()
        mgr.configure()
        mgr.get_secret("CLR")  # populate cache
        assert "CLR" in mgr._cache
        mgr.clear_cache("CLR")
        assert "CLR" not in mgr._cache

    def test_clear_cache_all(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EQUINOX_SECRET_A1", "v1")
        monkeypatch.setenv("EQUINOX_SECRET_B1", "v2")
        mgr = EnvironmentVariableManager()
        mgr.configure()
        mgr.get_secret("A1")
        mgr.get_secret("B1")
        mgr.clear_cache()
        assert len(mgr._cache) == 0

    def test_cache_disabled_does_not_cache(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EQUINOX_SECRET_NOCACHE", "v")
        mgr = EnvironmentVariableManager(enable_cache=False)
        mgr.configure()
        mgr.get_secret("NOCACHE")
        assert len(mgr._cache) == 0

    def test_validate_secret_length_too_long(self) -> None:
        mgr = EnvironmentVariableManager()
        huge = "x" * 2_000_000
        with pytest.raises(SecretManagerError, match="exceeds maximum"):
            mgr._validate_secret_length(huge, "big_secret")


# ── registry ─────────────────────────────────────────────────────────────────


class TestRegistry:
    def test_list_available_includes_env(self) -> None:
        managers = list_available_managers()
        assert "env" in managers

    def test_list_available_sorted(self) -> None:
        managers = list_available_managers()
        assert managers == sorted(managers)

    def test_get_secret_manager_env(self) -> None:
        mgr = get_secret_manager("env", enable_cache=False)
        assert mgr.is_available()

    def test_get_secret_manager_unknown_raises(self) -> None:
        with pytest.raises(SecretManagerError, match="Unknown"):
            get_secret_manager("totally_made_up_manager")

    def test_get_secret_manager_case_insensitive(self) -> None:
        mgr = get_secret_manager("ENV", enable_cache=False)
        assert mgr.is_available()

    def test_get_secret_manager_cached(self) -> None:
        mgr1 = get_secret_manager("env", enable_cache=True, cache_ttl=300)
        mgr2 = get_secret_manager("env", enable_cache=True, cache_ttl=300)
        assert mgr1 is mgr2  # same cached instance

    def test_register_custom_manager(self) -> None:
        class _MyManager(EnvironmentVariableManager):
            pass

        register_manager("test_custom_mgr", lambda: _MyManager)
        mgr = get_secret_manager("test_custom_mgr")
        assert isinstance(mgr, _MyManager)
        # Cleanup
        from equinox.core.secret_managers import registry as _reg

        _reg._SECRET_MANAGERS.pop("test_custom_mgr", None)
        _reg._instances.pop("test_custom_mgr:True:300", None)
