from __future__ import annotations

import json

import pytest
from equinox.core.exceptions import StorageError
from equinox.core.secret_managers import SecretNotFoundError
from equinox.storage import secret_integration as secret_integration_module


_SECRET_NAME_KEY = "_".join(("secret", "name"))
_SECRET_SOURCE_TYPE_KEY = "_".join(("secret", "source", "type"))
_SECRET_SOURCE_CONFIG_KEY = "_".join(("secret", "source", "config"))


class _FakeManager:
    def __init__(
        self,
        *,
        secret_value: str = "resolved-secret",
        secret_dict: dict[str, object] | None = None,
        secret_error: Exception | None = None,
        dict_error: Exception | None = None,
    ) -> None:
        self._secret_value = secret_value
        self._secret_dict = secret_dict or {}
        self._secret_error = secret_error
        self._dict_error = dict_error
        self.cache_cleared = False

    def get_secret(self, secret_name: str) -> str:
        del secret_name
        if self._secret_error is not None:
            raise self._secret_error
        return self._secret_value

    def get_secret_dict(self, secret_name: str) -> dict[str, object]:
        del secret_name
        if self._dict_error is not None:
            raise self._dict_error
        return dict(self._secret_dict)

    def clear_cache(self) -> None:
        self.cache_cleared = True


@pytest.fixture(autouse=True)
def _reset_global_resolver() -> None:
    secret_integration_module._global_resolver = None
    yield
    secret_integration_module._global_resolver = None


def test_get_manager_caches_initialized_manager(monkeypatch: pytest.MonkeyPatch) -> None:
    created_calls: list[tuple[str, int]] = []
    fake_manager = _FakeManager()

    def _factory(manager_type: str, cache_ttl: int) -> _FakeManager:
        created_calls.append((manager_type, cache_ttl))
        return fake_manager

    monkeypatch.setattr(secret_integration_module, "get_secret_manager", _factory)
    resolver = secret_integration_module.CredentialSecretResolver(cache_ttl=123)

    first = resolver.get_manager("env")
    second = resolver.get_manager("env")

    assert first is fake_manager
    assert second is fake_manager
    assert created_calls == [("env", 123)]


def test_get_manager_wraps_initialization_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def _factory(manager_type: str, cache_ttl: int) -> _FakeManager:
        del manager_type, cache_ttl
        raise RuntimeError("factory failed")

    monkeypatch.setattr(secret_integration_module, "get_secret_manager", _factory)
    resolver = secret_integration_module.CredentialSecretResolver()

    with pytest.raises(StorageError, match="Failed to initialize secret manager"):
        resolver.get_manager("vault")


def test_resolve_secret_value_rejects_empty_config() -> None:
    resolver = secret_integration_module.CredentialSecretResolver()
    resolver._managers["env"] = _FakeManager()

    with pytest.raises(StorageError, match="cannot be empty"):
        resolver.resolve_secret_value("env", {})


def test_resolve_secret_value_requires_identifier() -> None:
    resolver = secret_integration_module.CredentialSecretResolver()
    resolver._managers["env"] = _FakeManager()

    with pytest.raises(StorageError, match="must include 'secret_name' or 'path'"):
        resolver.resolve_secret_value("env", {"key": "token"})


def test_resolve_secret_value_reads_plain_string_secret() -> None:
    resolver = secret_integration_module.CredentialSecretResolver()
    resolver._managers["env"] = _FakeManager(secret_value="plain-secret")

    result = resolver.resolve_secret_value("env", {"secret_name": "prod/service/token"})

    assert result == "plain-secret"


def test_resolve_secret_value_extracts_named_key_from_json_secret() -> None:
    resolver = secret_integration_module.CredentialSecretResolver()
    resolver._managers["env"] = _FakeManager(secret_dict={"token": "abc123", "role": "reader"})

    result = resolver.resolve_secret_value(
        "env",
        {_SECRET_NAME_KEY: "prod/service/credentials", "key": "token"},  # pragma: allowlist secret
    )

    assert result == "abc123"


def test_resolve_secret_value_raises_when_named_key_missing() -> None:
    resolver = secret_integration_module.CredentialSecretResolver()
    resolver._managers["env"] = _FakeManager(secret_dict={"token": "abc123"})

    with pytest.raises(StorageError, match="Failed to retrieve secret"):
        resolver.resolve_secret_value(
            "env",
            {
                _SECRET_NAME_KEY: "prod/service/credentials",
                "key": "password",
            },  # pragma: allowlist secret
        )


def test_resolve_secret_value_validates_json_keys_and_returns_json_payload() -> None:
    resolver = secret_integration_module.CredentialSecretResolver()
    resolver._managers["env"] = _FakeManager(
        secret_dict={"username": "alice", "password": "s3cr3t"},
    )

    result = resolver.resolve_secret_value(
        "env",
        {
            _SECRET_NAME_KEY: "prod/service/db",
            "json_keys": ["username", "password"],
        },  # pragma: allowlist secret
    )

    assert json.loads(result) == {
        "username": "alice",
        "password": "s3cr3t",
    }  # pragma: allowlist secret


def test_resolve_secret_value_raises_when_required_json_key_missing() -> None:
    resolver = secret_integration_module.CredentialSecretResolver()
    resolver._managers["env"] = _FakeManager(secret_dict={"username": "alice"})

    with pytest.raises(StorageError, match="Failed to retrieve secret"):
        resolver.resolve_secret_value(
            "env",
            {
                _SECRET_NAME_KEY: "prod/service/db",
                "json_keys": ["username", "password"],
            },  # pragma: allowlist secret
        )


def test_resolve_secret_value_propagates_secret_not_found() -> None:
    resolver = secret_integration_module.CredentialSecretResolver()
    resolver._managers["env"] = _FakeManager(secret_error=SecretNotFoundError("missing"))

    with pytest.raises(SecretNotFoundError):
        resolver.resolve_secret_value(
            "env",
            {_SECRET_NAME_KEY: "missing-secret"},
        )  # pragma: allowlist secret


def test_resolve_secret_value_wraps_unexpected_errors() -> None:
    resolver = secret_integration_module.CredentialSecretResolver()
    resolver._managers["env"] = _FakeManager(secret_error=RuntimeError("backend exploded"))

    with pytest.raises(StorageError, match="Failed to retrieve secret"):
        resolver.resolve_secret_value(
            "env",
            {_SECRET_NAME_KEY: "prod/failing"},
        )  # pragma: allowlist secret


def test_hydrate_credential_without_secret_source_returns_original_row() -> None:
    resolver = secret_integration_module.CredentialSecretResolver()
    credential_row = {"id": 42, "auth_type": "bearer", "config": {"token": "local"}}

    result = resolver.hydrate_credential(credential_row)

    assert result is credential_row


def test_hydrate_credential_merges_plain_secret_value(monkeypatch: pytest.MonkeyPatch) -> None:
    resolver = secret_integration_module.CredentialSecretResolver()

    monkeypatch.setattr(
        resolver,
        "resolve_secret_value",
        lambda manager_type, config: "external-token",
    )

    credential_row = {
        "id": 7,
        "auth_type": "bearer",
        "config": {"existing": "value"},
        _SECRET_SOURCE_TYPE_KEY: "env",  # pragma: allowlist secret
        _SECRET_SOURCE_CONFIG_KEY: {_SECRET_NAME_KEY: "prod/token"},  # pragma: allowlist secret
    }

    result = resolver.hydrate_credential(credential_row)

    assert result is not credential_row
    assert result["config"] == {"existing": "value", "value": "external-token"}


def test_hydrate_credential_merges_json_secret_values(monkeypatch: pytest.MonkeyPatch) -> None:
    resolver = secret_integration_module.CredentialSecretResolver()

    monkeypatch.setattr(
        resolver,
        "resolve_secret_value",
        lambda manager_type, config: (
            '{"username": "alice", "password": "masked-value"}'
        ),  # pragma: allowlist secret
    )

    credential_row = {
        "id": 8,
        "auth_type": "basic",
        "config": {"realm": "prod"},
        _SECRET_SOURCE_TYPE_KEY: "vault",  # pragma: allowlist secret
        _SECRET_SOURCE_CONFIG_KEY: {"path": "secret/app", "json_keys": ["username", "password"]},
    }

    result = resolver.hydrate_credential(credential_row)

    assert result["config"] == {
        "realm": "prod",
        "username": "alice",
        "password": "masked-value",  # pragma: allowlist secret
    }


def test_hydrate_credential_reraises_resolution_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    resolver = secret_integration_module.CredentialSecretResolver()

    def _raise(manager_type: str, config: dict[str, object]) -> str:
        del manager_type, config
        raise StorageError("resolution failed")

    monkeypatch.setattr(resolver, "resolve_secret_value", _raise)

    credential_row = {
        "id": 9,
        "auth_type": "bearer",
        "config": {},
        _SECRET_SOURCE_TYPE_KEY: "env",  # pragma: allowlist secret
        _SECRET_SOURCE_CONFIG_KEY: {_SECRET_NAME_KEY: "prod/token"},  # pragma: allowlist secret
    }

    with pytest.raises(StorageError, match="resolution failed"):
        resolver.hydrate_credential(credential_row)


def test_load_credential_with_secrets_creates_default_resolver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Resolver:
        def hydrate_credential(self, credential_row: dict[str, object]) -> dict[str, object]:
            return {"hydrated": True, **credential_row}

    monkeypatch.setattr(secret_integration_module, "CredentialSecretResolver", _Resolver)

    result = secret_integration_module.load_credential_with_secrets({"id": 1})

    assert result == {"hydrated": True, "id": 1}


def test_create_auth_from_credential_with_secrets_uses_hydrated_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import equinox.auth as auth_module

    monkeypatch.setattr(
        secret_integration_module,
        "load_credential_with_secrets",
        lambda credential_row, resolver=None: {
            "auth_type": "bearer",
            "config": {"token": "abc123"},
        },
    )
    monkeypatch.setattr(
        auth_module,
        "auth_from_dict",
        lambda auth_type, config: {"type": auth_type, "config": config},
    )

    result = secret_integration_module.create_auth_from_credential_with_secrets({"id": 1})

    assert result == {"type": "bearer", "config": {"token": "abc123"}}


def test_create_auth_from_credential_with_secrets_requires_auth_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        secret_integration_module,
        "load_credential_with_secrets",
        lambda credential_row, resolver=None: {"config": {"token": "abc123"}},
    )

    with pytest.raises(StorageError, match="missing auth_type"):
        secret_integration_module.create_auth_from_credential_with_secrets({"id": 1})


def test_get_global_resolver_returns_singleton_instance() -> None:
    first = secret_integration_module.get_global_resolver()
    second = secret_integration_module.get_global_resolver()

    assert first is second


def test_clear_global_cache_clears_all_manager_caches() -> None:
    resolver = secret_integration_module.CredentialSecretResolver()
    first_manager = _FakeManager()
    second_manager = _FakeManager()
    resolver._managers = {"env": first_manager, "vault": second_manager}
    secret_integration_module._global_resolver = resolver

    secret_integration_module.clear_global_cache()

    assert first_manager.cache_cleared
    assert second_manager.cache_cleared


def test_security_secret_integration_wrapper_reexports_storage_symbols() -> None:
    import equinox.security.secret_integration as security_wrapper

    assert (
        security_wrapper.CredentialSecretResolver
        is secret_integration_module.CredentialSecretResolver
    )
