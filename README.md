# Equinox

Equinox is a secure, local-first API testing tool with both CLI and PyQt6 GUI workflows.

## Highlights

- Local-first storage (SQLite) with encrypted credential/auth persistence
- Rich request modeling (collections, folders, environments, variables)
- Built-in auth strategies (OAuth2, Bearer, API Key, Basic, AWS SigV4)
- Request/response history, assertions, captures, scripts, and code generation
- Plugin-ready architecture with security-focused validation defaults

## Architecture Snapshot

Current source layout is package-first:

```text
src/equinox/
  core/
	audit/
	codegen/
	interceptors/
	scripts/
	validation/
	client/
  gui/
	dialogs/
	request_panel/
	response_panel/
	syntax_highlighter/
	theme/
  storage/
	history/
```

Notes:

- Core modules were split from flat files into packages (`core/audit/`, `core/codegen/`, `core/interceptors/`, `core/scripts/`).
- Theme is now a package at `src/equinox/gui/theme/` (`__init__.py` re-exports the public API).

## Requirements

- Python 3.9+

## Installation

```bash
git clone https://github.com/i3iorn/equinox.git
cd equinox
pip install -e .
```

For development:

```bash
pip install -e ".[dev]"
pre-commit install
```

## Quick Start

```bash
equinox get https://api.example.com/users
equinox post https://api.example.com/users --json '{"name":"John"}'
equinox gui
```

## Development Workflow

Run this local quality gate before opening a PR:

```bash
pre-commit run --all-files
ruff check .
mypy src tests
pytest -q
```

## Examples

See `examples/` for sample collections, environments, plugins, and scripts.

## Troubleshooting

### History body-capture toggle can leak across tests

`set_capture_bodies(False)` in `core/history_config.py` updates process-global state. If a test disables it and does not reset it, later tests may unexpectedly store `None` for history bodies.

Recommended teardown/finalizer reset:

```python
from equinox.core.history_config import set_capture_bodies

set_capture_bodies(True)
```

## Contributing

Contributions are welcome. Please open an issue or submit a PR with a clear change description.

## Security

If you discover a vulnerability, open a private GitHub security advisory.

## License

MIT License. See `LICENSE`.
