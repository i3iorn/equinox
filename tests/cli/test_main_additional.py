from __future__ import annotations

import types

from click import Command
from click.testing import CliRunner
from typing import cast

import pytest

import equinox.cli.main as cli_main


CLI_MAIN = cast(Command, cli_main.main)


def test_gui_command_invokes_gui_main(monkeypatch: pytest.MonkeyPatch) -> None:
    called = {"ok": False}

    fake_mod = types.SimpleNamespace(main=lambda: called.__setitem__("ok", True))
    monkeypatch.setitem(__import__("sys").modules, "equinox.gui.app", fake_mod)

    runner = CliRunner()
    result = runner.invoke(CLI_MAIN, ["gui"])

    assert result.exit_code == 0
    assert called["ok"] is True


def test_rotate_secrets_missing_db_path_exits_with_code_2() -> None:
    runner = CliRunner()
    result = runner.invoke(CLI_MAIN, ["rotate-secrets", "--new-password", "pw"])

    assert result.exit_code == 2
    assert "--db-path is required" in result.output


def test_rotate_secrets_function_missing_password_exits_with_code_1() -> None:
    callback = cli_main.rotate_secrets.callback
    assert callback is not None
    with pytest.raises(SystemExit) as exc_info:
        callback("test.db", None)

    assert exc_info.value.code == 1


def test_main_entry_calls_click_group(monkeypatch: pytest.MonkeyPatch) -> None:
    called = {"ok": False}

    def _fake_main() -> None:
        called["ok"] = True

    monkeypatch.setattr(cli_main, "main", _fake_main)
    cli_main.main_entry()

    assert called["ok"] is True

