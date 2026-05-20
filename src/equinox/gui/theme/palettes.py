"""Theme palettes, mode constants, and the dynamic ``Colors`` proxy."""

from __future__ import annotations

THEME_SYSTEM = "system"
THEME_LIGHT = "light"
THEME_DARK = "dark"
THEME_MUTED_DARK = "muted_dark"
THEME_OCEANIC = "oceanic"

THEME_MODES = (
    THEME_SYSTEM,
    THEME_LIGHT,
    THEME_DARK,
    THEME_MUTED_DARK,
    THEME_OCEANIC,
)

THEME_LABELS = {
    THEME_SYSTEM: "System",
    THEME_LIGHT: "Light",
    THEME_DARK: "Dark",
    THEME_MUTED_DARK: "Muted Dark",
    THEME_OCEANIC: "Oceanic (Deep Blue)",
}

_LIGHT: dict[str, str] = {
    "GREEN": "#1a7f37",
    "AMBER": "#9a6700",
    "RED": "#cf222e",
    "BLUE": "#0550ae",
    "PURPLE": "#8250df",
    "MUTED": "#656d76",
    "CYAN": "#006b75",
    "GRAY": "#57606a",
    "TEAL": "#008080",
    "BG": "#f7f7f8",
    "BG_ALT": "#ededef",
    "BORDER": "#d1d9e0",
    "BORDER_FCS": "#0969da",
    "FG": "#1f2328",
    "FG_MUTED": "#4d5561",
    "FG_SUBTLE": "#6e7681",
    "SELECTION": "#dceefb",
    "SEL_TEXT": "#b6d7f8",
    "HIGHLIGHT": "#fff3a3",
    "SEND_HOVER": "#0860ca",
}

_DARK: dict[str, str] = {
    "GREEN": "#2da44e",
    "AMBER": "#b8860b",
    "RED": "#e0484b",
    "BLUE": "#6daef4",
    "PURPLE": "#9a7bdb",
    "MUTED": "#8b949e",
    "CYAN": "#39c5cf",
    "GRAY": "#6e7681",
    "TEAL": "#39c5cf",
    "BG": "#0d1117",
    "BG_ALT": "#161b22",
    "BORDER": "#30363d",
    "BORDER_FCS": "#4d8ed4",
    "FG": "#e6edf3",
    "FG_MUTED": "#b0b9c3",
    "FG_SUBTLE": "#8b949e",
    "SELECTION": "#1f3347",
    "SEL_TEXT": "#264f78",
    "HIGHLIGHT": "#5a4a28",
    "SEND_HOVER": "#6ab5eb",
}

_MUTED_DARK: dict[str, str] = {
    "GREEN": "#26a641",
    "AMBER": "#9d8501",
    "RED": "#d1444f",
    "BLUE": "#5fa3e8",
    "PURPLE": "#8b7ec1",
    "MUTED": "#787e87",
    "CYAN": "#3ba7ac",
    "GRAY": "#6e7681",
    "TEAL": "#3ba7ac",
    "BG": "#0a0e13",
    "BG_ALT": "#131820",
    "BORDER": "#353c47",
    "BORDER_FCS": "#6da6f0",
    "FG": "#f0f2f5",
    "FG_MUTED": "#c9cdd4",
    "FG_SUBTLE": "#8e95a3",
    "SELECTION": "#1a2e42",
    "SEL_TEXT": "#3d5a7a",
    "HIGHLIGHT": "#4a3d1f",
    "SEND_HOVER": "#5d95dc",
}

_OCEANIC: dict[str, str] = {
    "GREEN": "#40c463",
    "AMBER": "#e3b341",
    "RED": "#f85149",
    "BLUE": "#58a6ff",
    "PURPLE": "#bc8cff",
    "MUTED": "#8b949e",
    "CYAN": "#39c5cf",
    "GRAY": "#6e7681",
    "TEAL": "#56d4dd",
    "BG": "#011627",
    "BG_ALT": "#011f35",
    "BORDER": "#1d3b53",
    "BORDER_FCS": "#00d1ff",
    "FG": "#d6deeb",
    "FG_MUTED": "#92a1b5",
    "FG_SUBTLE": "#5f7e97",
    "SELECTION": "#1d3b53",
    "SEL_TEXT": "#234d70",
    "HIGHLIGHT": "#0b2942",
    "SEND_HOVER": "#70b1ff",
}


def validate_palettes() -> None:
    """Fail fast if any palette is missing or adding keys."""
    palettes = {
        "LIGHT": _LIGHT,
        "DARK": _DARK,
        "MUTED_DARK": _MUTED_DARK,
        "OCEANIC": _OCEANIC,
    }
    keys = set(_LIGHT.keys())
    for name, palette in palettes.items():
        missing = keys - set(palette.keys())
        extra = set(palette.keys()) - keys
        if missing or extra:
            raise ValueError(f"Palette {name} mismatch: missing={missing}, extra={extra}")


validate_palettes()

_active: dict[str, str] = dict(_LIGHT)


def set_active_palette(palette: dict[str, str]) -> None:
    global _active
    _active = palette


def get_active_palette() -> dict[str, str]:
    return _active


class _ColorProxy:
    @property
    def SUCCESS(self) -> str:
        return _active["GREEN"]

    @property
    def WARNING(self) -> str:
        return _active["AMBER"]

    @property
    def ERROR(self) -> str:
        return _active["RED"]

    @property
    def INFO(self) -> str:
        return _active["BLUE"]

    @property
    def METHOD(self) -> dict[str, str]:
        p = _active
        return {
            "GET": p["GREEN"],
            "POST": p["AMBER"],
            "PUT": p["BLUE"],
            "PATCH": p["PURPLE"],
            "DELETE": p["RED"],
            "HEAD": p["MUTED"],
            "OPTIONS": p["MUTED"],
        }

    def __getattr__(self, name: str) -> str:
        try:
            return _active[name]
        except KeyError as exc:
            raise AttributeError(f"Colors has no attribute {name!r}") from exc


Colors = _ColorProxy()


def resolve_palette(mode: str, system_dark: bool) -> dict[str, str]:
    if mode == THEME_MUTED_DARK:
        return _MUTED_DARK
    if mode == THEME_DARK:
        return _DARK
    if mode == THEME_OCEANIC:
        return _OCEANIC
    if mode == THEME_LIGHT:
        return _LIGHT
    return _DARK if system_dark else _LIGHT


def palette_cache_key(palette: dict[str, str]) -> str:
    if palette is _LIGHT:
        return THEME_LIGHT
    if palette is _DARK:
        return THEME_DARK
    if palette is _MUTED_DARK:
        return THEME_MUTED_DARK
    if palette is _OCEANIC:
        return THEME_OCEANIC
    return "custom"


def is_dark_mode(mode: str, system_dark: bool) -> bool:
    if mode in (THEME_DARK, THEME_MUTED_DARK, THEME_OCEANIC):
        return True
    if mode == THEME_LIGHT:
        return False
    return system_dark
