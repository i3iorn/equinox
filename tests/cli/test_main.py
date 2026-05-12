from click.testing import CliRunner
from click import Command
from typing import cast

from equinox.cli.main import main


CLI_MAIN = cast(Command, main)


def test_rotate_secrets_prompts_for_password(monkeypatch):
    called = {}

    def _fake_rotate(db_path, new_password):
        called["db_path"] = db_path
        called["new_password"] = new_password

    monkeypatch.setattr("equinox.cli.main.rotate_all_secrets", _fake_rotate)

    runner = CliRunner()
    result = runner.invoke(
        CLI_MAIN,
        ["rotate-secrets", "--db-path", "test.db"],
        input="p@ssw0rd\np@ssw0rd\n",
    )

    assert result.exit_code == 0
    assert called["db_path"] == "test.db"
    assert called["new_password"] == "p@ssw0rd"
    assert "Secret rotation completed." in result.output


def test_rotate_secrets_accepts_explicit_password(monkeypatch):
    called = {}

    def _fake_rotate(db_path, new_password):
        called["db_path"] = db_path
        called["new_password"] = new_password

    monkeypatch.setattr("equinox.cli.main.rotate_all_secrets", _fake_rotate)

    runner = CliRunner()
    result = runner.invoke(
        CLI_MAIN,
        ["rotate-secrets", "--db-path", "test.db", "--new-password", "cli-provided"],
    )

    assert result.exit_code == 0
    assert called["db_path"] == "test.db"
    assert called["new_password"] == "cli-provided"

