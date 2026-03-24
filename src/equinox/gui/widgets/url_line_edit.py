"""URL input with ghost query-parameter preview."""


from PyQt6.QtWidgets import QLineEdit, QToolTip
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPainter, QColor, QFontMetricsF

from equinox.storage import EnvironmentManager
from equinox.core.interpolation import VariableInterpolator
from equinox.storage import CollectionManager
import os
import re
from typing import Optional

from equinox.gui.theme import Colors


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
        painter.setPen(QColor(Colors.FG_SUBTLE))
        painter.setFont(self.font())
        painter.drawText(
            suffix_x, 0, available_width, self.height(),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            self._param_suffix,
        )
        painter.end()

    def mouseMoveEvent(self, event):
        """Show tooltip for a {{variable}} under the mouse cursor.

        We map the mouse position to a character index in the line edit, then
        scan outward for a {{...}} token. If found, we interpolate it against
        the active environment and show the resolved value as a tooltip.
        """
        try:
            pos = event.pos()
            # cursorPositionAt maps QPoint to character index
            idx = self.cursorPositionAt(pos)
            text = self.text()
            if not text or idx < 0 or idx > len(text):
                return super().mouseMoveEvent(event)

            # Find the nearest {{ on the left and }} on the right
            left = text.rfind("{{", 0, idx)
            right = text.find("}}", idx)
            if left == -1 or right == -1:
                # No token under cursor — clear last seen to allow future updates
                self._last_hovered_var = None
                return super().mouseMoveEvent(event)

            token = text[left : right + 2]
            # Basic validation of token structure
            if not (token.startswith("{{") and token.endswith("}}")):
                self._last_hovered_var = None
                return super().mouseMoveEvent(event)

            var_name = token[2:-2].strip()
            if not var_name:
                self._last_hovered_var = None
                return super().mouseMoveEvent(event)

            # Avoid re-showing tooltip for the same variable repeatedly
            if var_name == self._last_hovered_var:
                return super().mouseMoveEvent(event)

            # Resolve using the same variable resolution pipeline the
            # request sender uses: collection vars (if any) + active env
            # variables + filtered OS env + session vars. This ensures the
            # tooltip matches what the request will see when sent.
            try:
                win = self.window()
                db = getattr(win, "db", None)
                rp = getattr(win, "request_panel", None)
                resolved = token
                if db is None:
                    # No DB available — nothing we can do
                    resolved = token
                else:
                    variables = {}
                    # Active environment variables
                    try:
                        env_mgr = EnvironmentManager(db)
                        active = env_mgr.get_active_environment()
                        if active:
                            variables.update(active.get("variables", {}))
                    except Exception:
                        pass

                    # Collection / inherited variables (if request loaded)
                    try:
                        if rp is not None and getattr(rp, "current_request", None) and getattr(rp.current_request, "collection_id", None):
                            col_mgr = CollectionManager(db)
                            col_vars = col_mgr.get_all_collection_variables(rp.current_request.collection_id)
                            variables.update(col_vars)
                    except Exception:
                        pass

                    # Filter OS env to variable-like names (same pattern as sender)
                    try:
                        valid_var_pattern = re.compile(r'^[a-zA-Z0-9_-]+$')
                        os_env_filtered = {
                            k: v for k, v in os.environ.items()
                            if isinstance(v, str) and valid_var_pattern.match(k)
                        }
                        if os_env_filtered:
                            variables.update(os_env_filtered)
                    except Exception:
                        pass

                    # Session vars captured in the RequestPanel (override env)
                    try:
                        if rp is not None:
                            session_vars = rp.get_session_vars()
                            variables.update(session_vars)
                    except Exception:
                        pass

                    # Finally run the same interpolation routine
                    try:
                        resolved = VariableInterpolator.interpolate(token, variables)
                    except Exception:
                        resolved = token
            except Exception:
                resolved = token

            # Build tooltip text: show variable name → resolved value
            tip = f"{var_name} → {resolved}"
            QToolTip.showText(self.mapToGlobal(pos), tip, self)
            self._last_hovered_var = var_name
        except Exception:
            # Any error should not break UI
            self._last_hovered_var = None

        # Always pass the event along to the base class (don't `return` from
        # inside a finally clause — that can change control flow unexpectedly).
        super().mouseMoveEvent(event)

