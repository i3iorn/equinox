"""URL input with ghost query-parameter preview."""

import logging
import os
import re
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFontMetricsF, QPainter
from PyQt6.QtWidgets import QLineEdit, QToolTip

from equinox.core.interpolation import VariableInterpolator
from equinox.gui.theme import Colors
from equinox.storage import CollectionManager, EnvironmentManager

logger = logging.getLogger(__name__)

# Compiled once; reused for every OS-env key filter during tooltip resolution.
_VALID_VAR_RE = re.compile(r"^[a-zA-Z0-9_-]+$")

# Internal geometry constants — must match the QSS: border 1 px + padding 6 px
_LEFT_MARGIN: int = 7
_RIGHT_MARGIN: int = 7


# ---------------------------------------------------------------------------
# Encapsulated variable resolution
# ---------------------------------------------------------------------------

class _VariableResolver:
    """Encapsulates multi-source variable resolution with error tracking.

    Resolves ``{{var}}`` tokens from active environment, collection settings,
    database state, OS environment, and session state in the documented order.
    Logs warnings for each step that fails, ensuring visibility during debugging.
    """

    def __init__(self, url_edit: "UrlLineEdit") -> None:
        self._url_edit = url_edit

    def resolve(self, var_name: str, token: str) -> str:
        """Return the resolved value of *token*, or *token* unchanged if unresolvable."""
        variables: dict[str, str] = {}

        # 1. Active database environment.
        self._merge_active_env(variables)

        # 2. Collection-level variables (from the loaded request's collection).
        self._merge_collection_vars(variables)

        # 3. OS environment — only valid identifier keys are allowed.
        self._merge_os_vars(variables, var_name)

        # 4. Session variables captured in RequestPanel.
        self._merge_session_vars(variables)

        # 5. Path parameters (highest precedence for URL-embedded tokens).
        self._merge_path_params(variables)

        try:
            return VariableInterpolator.interpolate(token, variables)
        except Exception as e:
            logger.debug("VariableInterpolator.interpolate failed: %s", e)
            return token

    def _merge_active_env(self, variables: dict[str, str]) -> None:
        """Load variables from the active database environment."""
        try:
            db = self._get_db()
            if db is None:
                return
            env = EnvironmentManager(db).get_active_environment()
            if env:
                variables.update(env.get("variables", {}))
        except Exception as e:
            logger.debug("Failed to load active environment: %s", e)

    def _merge_collection_vars(self, variables: dict[str, str]) -> None:
        """Load collection-level variables from the current request."""
        try:
            db = self._get_db()
            rp = self._get_request_panel()
            if db is None or rp is None:
                return
            req = getattr(rp, "current_request", None)
            col_id = getattr(req, "collection_id", None) if req else None
            if col_id is not None:
                variables.update(
                    CollectionManager(db).get_all_collection_variables(col_id)
                )
        except Exception as e:
            logger.debug("Failed to load collection variables: %s", e)

    def _merge_os_vars(self, variables: dict[str, str], var_name: str) -> None:
        """Load the specific OS environment variable (guarded by name validation)."""
        try:
            # Only permit lookup of reasonably-named variables to avoid accidents.
            if _VALID_VAR_RE.match(var_name):
                os_val = os.environ.get(var_name)
                if os_val is not None:
                    variables[var_name] = os_val
        except Exception as e:
            logger.debug("Failed to load OS environment variable %r: %s", var_name, e)

    def _merge_session_vars(self, variables: dict[str, str]) -> None:
        """Load session variables captured in RequestPanel."""
        try:
            rp = self._get_request_panel()
            if rp is not None:
                variables.update(rp.get_session_vars())
        except Exception as e:
            logger.debug("Failed to load session variables: %s", e)

    def _merge_path_params(self, variables: dict[str, str]) -> None:
        """Load path parameters (highest precedence)."""
        try:
            rp = self._get_request_panel()
            if rp is not None and getattr(rp, "path_params_table", None) is not None:
                path_params = rp.path_params_table.get_all_data()
                if path_params:
                    variables.update(path_params)
        except Exception as e:
            logger.debug("Failed to load path parameters: %s", e)

    def _get_db(self):
        """Return the main window's database, or None if unavailable."""
        try:
            win = self._url_edit.window()
            return getattr(win, "db", None)
        except Exception:
            return None

    def _get_request_panel(self):
        """Return the main window's request panel, or None if unavailable."""
        try:
            win = self._url_edit.window()
            return getattr(win, "request_panel", None)
        except Exception:
            return None


# ---------------------------------------------------------------------------
# Widget
# ---------------------------------------------------------------------------

class UrlLineEdit(QLineEdit):
    """URL bar that renders active query parameters as a ghost suffix.

    When the field is **not** focused, enabled params from the Params table are
    painted after the URL text in ``Colors.FG_SUBTLE``, giving the user a live
    preview of the full request URL without polluting the actual text value.

    When the field **receives focus** the ghost is hidden so the user edits
    the clean URL string without visual clutter.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._param_suffix: str = ""
        # Enable mouse move events even when no button is pressed so we can
        # show tooltips for variable placeholders under the cursor.
        self.setMouseTracking(True)
        self._last_hovered_var: Optional[str] = None
        self._var_resolver = _VariableResolver(self)

    def set_param_suffix(self, suffix: str) -> None:
        """Update the ghost params string and repaint."""
        if suffix != self._param_suffix:
            self._param_suffix = suffix
            self.update()

    def focusInEvent(self, event) -> None:  # noqa: N802
        super().focusInEvent(event)
        self.update()  # hide ghost while editing

    def focusOutEvent(self, event) -> None:  # noqa: N802
        super().focusOutEvent(event)
        self.update()  # show ghost again

    def leaveEvent(self, event) -> None:  # noqa: N802
        """Reset hover state so the tooltip reappears on re-entry."""
        self._last_hovered_var = None
        QToolTip.hideText()
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        if self.hasFocus() or not self._param_suffix:
            return

        fm = QFontMetricsF(self.font())
        url_width = fm.horizontalAdvance(self.text())

        # If the URL is already scrolled (wider than visible area) the suffix
        # can't be reliably positioned, so skip it.
        visible_width = self.width() - _LEFT_MARGIN - _RIGHT_MARGIN
        if url_width > visible_width:
            return

        suffix_x = _LEFT_MARGIN + int(url_width)
        available_width = self.width() - suffix_x - _RIGHT_MARGIN
        if available_width < 16:
            return

        painter = QPainter(self)
        try:
            painter.setPen(QColor(Colors.FG_SUBTLE))
            painter.setFont(self.font())
            painter.drawText(
                suffix_x,
                0,
                available_width,
                self.height(),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                self._param_suffix,
            )
        finally:
            painter.end()

    # ── Variable tooltip ──────────────────────────────────────────────

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
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

                if left == -1 or right == -1 or right <= left + 2:
                    self._last_hovered_var = None
                else:
                    var_name = text[left + 2 : right].strip()
                    if not var_name:
                        self._last_hovered_var = None
                    elif var_name != self._last_hovered_var:
                        token = text[left : right + 2]
                        resolved = self._var_resolver.resolve(var_name, token)
                        if resolved == token:
                            tip = f"{var_name}  (not set)"
                        else:
                            tip = f"{var_name} → {resolved}"
                        QToolTip.showText(self.mapToGlobal(pos), tip, self)
                        self._last_hovered_var = var_name
        except Exception as e:
            logger.debug("mouseMoveEvent exception: %s", e)
            self._last_hovered_var = None

        super().mouseMoveEvent(event)

