"""PyQt6 GUI application for Equinox"""

def main() -> int:
	"""Run the GUI entrypoint with lazy import to avoid import-time side effects."""
	from equinox.gui.app import main as _main

	return _main()

__all__ = ["main"]
