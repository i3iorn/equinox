"""URL input with ghost query-parameter preview."""

import os
import re
from typing import Optional

from PyQt6.QtWidgets import QLineEdit, QToolTip
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPainter, QColor, QFontMetricsF

from equinox.core.interpolation import VariableInterpolator
from equinox.storage import CollectionManager, EnvironmentManager
from equinox.gui.theme import Colors

# Compiled once; reused for every OS-env key filter during tooltip resolution.
_VALID_VAR_RE = re.compile(r'^[a-zA-Z0-9_-]+$')


class UrlLineEdit(QLineEdit):
    """URL bar that renders active query parameters as a ghost suffix.

    When the field is **not** focused, enabled params from the Params table are
    painted after the URL text in ``Colors.FG_SUBTLE``, giving the user a live
    preview of the full request URL without polluting the actual text value.

    When the field **receives focus** the ghost is hidden so the user edits
    the clean URL string without visual clutter.
    """

    # Internal geometry constants — must match the QSS: border 1 px + padding 6 px
    _LEFT = 7
    _RIGHT = 7

    def __init__(self, parent=None):
        super().__init__(parent)
        self._param_suffix: str = ""
        # Enable mouse move events even when no button is pressed so we can
        # show tooltips for variable placeholders under the cursor.
        self.setMouseTracking(True)
        self._last_hovered_var: Optional[str] = None

    def set_param_suffix(self, suffix: str) -> None:
        """Update the ghost params string and repaint."""
        if suffix != self._param_suffix:
            self._param_suffix = suffix
            self.update()

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.update()   # hide ghost while editing

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.update()   # show ghost again

    def leaveEvent(self, event):
        """Reset hover state so the tooltip reappears on re-entry."""
        self._last_hovered_var = None
        QToolTip.hideText()
        super().leaveEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.hasFocus() or not self._param_suffix:
            return

        fm = QFontMetricsF(self.font())
        url_width = fm.horizontalAdvance(self.text())

        # If the URL is already scrolled (wider than visible area) the suffix
        # can't be reliably positioned, so skip it.
        visible_width = self.width() - self._LEFT - self._RIGHT
        if url_width > visible_width:
            return

        suffix_x = self._LEFT + int(url_width)
        available_width = self.width() - suffix_x - self._RIGHT
        if available_width < 16:
            return

        painter = QPainter(self)
        try:
            painter.setPen(QColor(Colors.FG_SUBTLE))
            painter.setFont(self.font())
            painter.drawText(
                suffix_x, 0, available_width, self.height(),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                self._param_suffix,
            )
        finally:
            painter.end()

    # ── Variable tooltip ──────────────────────────────────────────────

    def mouseMoveEvent(self, event):
        """Show a tooltip for the ``{{variable}}`` under the mouse cursor."""
        try:
            pos = event.pos()
            idx = self.cursorPositionAt(pos)
            text = self.text()

            if not text or idx < 0:
                self._last_hovered_var = None
            else:
                # Find the nearest {{ on the left and }} on the right.
                left = text.rfind("{{", 0, idx)
                right = text.find("}}", idx)

                if left == -1 or right == -1:
                    self._last_hovered_var = None
                else:
                    var_name = text[left + 2 : right].strip()
                    if not var_name:
                        self._last_hovered_var = None
                    elif var_name != self._last_hovered_var:
                        token = text[left : right + 2]
                        resolved = self._resolve_variable(var_name, token)
                        if resolved == token:
                            tip = f"{var_name}  (not set)"
                        else:
                            tip = f"{var_name} → {resolved}"
                        QToolTip.showText(self.mapToGlobal(pos), tip, self)
                        self._last_hovered_var = var_name
        except Exception:
            self._last_hovered_var = None

        super().mouseMoveEvent(event)

    def _resolve_variable(self, var_name: str, token: str) -> str:
        """Interpolate *token* against the same variable sources the request sender uses.

        Returns the resolved string, or *token* unchanged when resolution fails
        or no database is available.
        """
        win = self.window()
        db = getattr(win, "db", None)
        if db is None:
            return token

        rp = getattr(win, "request_panel", None)
        variables: dict = {}

        # 1. Active environment variables.
        try:
            env = EnvironmentManager(db).get_active_environment()
            if env:
                variables.update(env.get("variables", {}))
        except Exception:
            pass

        # 2. Collection-level variables (if a collection request is loaded).
        try:
            if rp is not None:
                req = getattr(rp, "current_request", None)
                col_id = getattr(req, "collection_id", None) if req else None
                if col_id is not None:
                    variables.update(
                        CollectionManager(db).get_all_collection_variables(col_id)
                    )
        except Exception:
            pass

        # 3. OS environment — look up only the specific variable rather than
        #    iterating all of os.environ to avoid an unnecessary linear scan.
        try:
            if _VALID_VAR_RE.match(var_name):
                os_val = os.environ.get(var_name)
                if os_val is not None:
                    variables[var_name] = os_val
        except Exception:
            pass

        # 4. Session variables captured in the RequestPanel (override env).
        try:
            if rp is not None:
                variables.update(rp.get_session_vars())
        except Exception:
            pass

        # 5. Path parameters take highest precedence for URL-embedded tokens.
        try:
            if rp is not None and getattr(rp, "path_params_table", None) is not None:
                path_params = rp.path_params_table.get_all_data()
                if path_params:
                    variables.update(path_params)
        except Exception:
            pass

        try:
            return VariableInterpolator.interpolate(token, variables)
        except Exception:
            return token
