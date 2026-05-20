from equinox.security import secrets_password


def _reset_master_password_state(monkeypatch) -> None:
    monkeypatch.setattr(secrets_password, "_cached_password", None)
    monkeypatch.setattr(secrets_password, "_cached_fernet", None)
    monkeypatch.setattr(secrets_password, "_password_prompt_callback", None)


def test_prompt_callback_is_used_without_getpass(monkeypatch):
    _reset_master_password_state(monkeypatch)
    monkeypatch.delenv("EQUINOX_MASTER_PASSWORD", raising=False)

    prompt_calls = {"count": 0}

    def _prompt() -> str:
        prompt_calls["count"] += 1
        return "gui-secret"

    def _fail_getpass(_: str) -> str:
        raise AssertionError("getpass should not be called when GUI callback is set")

    monkeypatch.setattr(secrets_password, "getpass", _fail_getpass)
    secrets_password.set_master_password_prompt(_prompt)

    assert secrets_password.get_master_password() == "gui-secret"
    assert secrets_password.get_master_password() == "gui-secret"
    assert prompt_calls["count"] == 1


def test_prompt_callback_cancel_does_not_fallback_to_getpass(monkeypatch):
    _reset_master_password_state(monkeypatch)
    monkeypatch.delenv("EQUINOX_MASTER_PASSWORD", raising=False)

    def _prompt_none():
        return None

    def _fail_getpass(_: str) -> str:
        raise AssertionError("getpass should not be called after GUI prompt cancellation")

    monkeypatch.setattr(secrets_password, "getpass", _fail_getpass)
    secrets_password.set_master_password_prompt(_prompt_none)

    assert secrets_password.get_master_password() is None
