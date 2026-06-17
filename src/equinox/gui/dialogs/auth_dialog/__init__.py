from equinox.auth import AuthStrategy
from equinox.storage import Database
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QDialog
from PyQt6.QtWidgets import QHBoxLayout
from PyQt6.QtWidgets import QPushButton
from PyQt6.QtWidgets import QTabWidget
from PyQt6.QtWidgets import QVBoxLayout
from PyQt6.QtWidgets import QWidget

from .oauth2.controller import OAuth2TokenController
from .tabs.api_key import ApiKeyAuthTab
from .tabs.aws import AwsSigV4AuthTab
from .tabs.basic import BasicAuthTab
from .tabs.bearer import BearerAuthTab
from .tabs.oauth2 import OAuth2AuthTab

class AuthDialog(QDialog):
    auth_configured = pyqtSignal(object)

    def __init__(self, current_auth: AuthStrategy | None = None, parent: QWidget | None = None, db: Database | None = None) -> None:
        super().__init__(parent)
        self.db = db
        self.current_auth = current_auth

        self.setWindowTitle("Configure Authentication")
        self.setMinimumSize(540, 480)

        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()

        self.no_auth = QWidget()
        self.basic = BasicAuthTab()
        self.bearer = BearerAuthTab()
        self.oauth2 = OAuth2AuthTab()
        self.api_key = ApiKeyAuthTab()
        self.aws = AwsSigV4AuthTab()

        self.tabs.addTab(self.no_auth, "No Auth")
        self.tabs.addTab(self.basic, "Basic Auth")
        self.tabs.addTab(self.bearer, "Bearer Token")
        self.tabs.addTab(self.oauth2, "OAuth 2.0")
        self.tabs.addTab(self.api_key, "API Key")
        self.tabs.addTab(self.aws, "AWS SigV4")

        layout.addWidget(self.tabs)

        # OAuth2 controller
        self.oauth2_controller = OAuth2TokenController(self.oauth2, db=db, parent=self)

        btns = QHBoxLayout()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Save")
        save.clicked.connect(self._save_auth)
        btns.addStretch()
        btns.addWidget(cancel)
        btns.addWidget(save)
        layout.addLayout(btns)

    def _save_auth(self) -> None:
        tab = self.tabs.currentWidget()
        if tab is None:
            return

        if hasattr(tab, "get_auth_config"):
            config = tab.get_auth_config()
            self.accept()
            self.auth_configured.emit(config)
