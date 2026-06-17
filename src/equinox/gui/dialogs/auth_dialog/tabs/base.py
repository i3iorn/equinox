from typing import Protocol


class AuthDialogTab(Protocol):
    def get_auth_config(self) -> dict[str, str] | dict[str, str | None] | dict[str, str | bool | int] | dict[str, str | bool | None]:...
