from equinox.auth import OAuth2Auth
from equinox.core.exceptions import AuthError
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtCore import QObject
from PyQt6.QtCore import QThread

class OAuth2TokenFetchWorker(QThread):
    finished = pyqtSignal(object)

    def __init__(self, auth: OAuth2Auth, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.auth = auth

    def run(self) -> None:
        try:
            self.auth.apply(object(), {})
            self.finished.emit({"ok": True, "auth": self.auth, "response": self.auth.last_token_response})
        except Exception as exc:
            response = getattr(self.auth, "last_token_response", None)
            if response is None and isinstance(exc, AuthError):
                response = exc.details.get("token_response")
            self.finished.emit({"ok": False, "auth": self.auth, "error": str(exc), "response": response})
