"""Stylesheet generation from resolved palette values."""

from __future__ import annotations

from pathlib import Path

from .settings import get_small_text_size


def _pad(base_px: int, density_scale: float) -> str:
    """Return a padding value scaled by density, as a CSS ``px`` string."""
    return f"{max(1, round(base_px * density_scale))}px"


def build_stylesheet(base_pt: int, colors: dict[str, str], density_scale: float = 1.0) -> str:
    """Generate the application-wide stylesheet string."""
    c = colors
    sm = get_small_text_size(base_pt)
    me = int(sm * 1.2)

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

    stylesheet = stylesheet.replace("{SM}", str(sm)).replace("{ME}", str(me))

    # Density-scaled padding variables.
    stylesheet = stylesheet.replace("{PAD_BTN}", _pad(6, density_scale))
    stylesheet = stylesheet.replace("{PAD_INPUT}", _pad(5, density_scale))
    stylesheet = stylesheet.replace("{PAD_GROUP}", _pad(10, density_scale))
    stylesheet = stylesheet.replace("{PAD_FORM}", _pad(8, density_scale))
    stylesheet = stylesheet.replace("{PAD_TAB}", _pad(10, density_scale))

    return stylesheet
