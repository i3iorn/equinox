from PyQt6.QtWidgets import QWidget


class AuthDialogTab(QWidget):
    def get_auth_config(
        self,
    ) -> (
        dict[str, str]
        | dict[str, str | None]
        | dict[str, str | bool | int]
        | dict[str, str | bool | None]
    ):
        """Return the authentication configuration as a dictionary.

        Returns:
            dict: A dictionary containing the authentication configuration.
        """
        raise NotImplementedError("Subclasses must implement get_auth_config()")
