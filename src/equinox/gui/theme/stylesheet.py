"""Stylesheet generation from resolved palette values."""

from __future__ import annotations

from pathlib import Path

from .settings import get_small_text_size


def build_stylesheet(base_pt: int, colors: dict[str, str]) -> str:
    """Generate the application-wide stylesheet string."""
    c = colors
    sm = get_small_text_size(base_pt)
    me = int(sm*1.2)

    stylesheet_path = Path(__file__).parent / "stylesheet.qss"
    stylesheet = stylesheet_path.read_text()

    variables_used = [
        "AMBER",
        "BG",
        "BG_ALT",
        "BLUE",
        "BORDER",
        "BORDER_FCS",
        "FG",
        "FG_MUTED",
        "FG_SUBTLE",
        "GREEN",
        "RED",
        "SELECTION",
        "SEND_HOVER",
    ]

    for name in variables_used:
        pattern = "{" + name + "}"
        stylesheet = stylesheet.replace(pattern, c[name])

    stylesheet.replace("{SM}", str(sm)).replace("{ME}", str(me))

    return stylesheet
