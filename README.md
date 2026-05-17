# Equinox

Equinox is a secure, local-first API testing tool with both CLI and PyQt6 GUI workflows.

## Highlights

- Local-first storage (SQLite) with encrypted credential/auth persistence
- Rich request modeling (collections, folders, environments, variables)
- Built-in auth strategies (OAuth2, Bearer, API Key, Basic, AWS SigV4)
- Request/response history, assertions, captures, scripts, and code generation
- Plugin-ready architecture with security-focused validation defaults

## Architecture Snapshot

Source is organized by cohesive packages:

```text
src/equinox/
  core/
	audit/
	client/
	codegen/
	interceptors/
	scripts/
	validation/
  gui/
	dialogs/
	request_panel/
	response_panel/
	syntax_highlighter/
	theme/
  storage/
	collections/
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
python -m equinox.gui.app
equinox rotate-secrets --help
```

## Development Workflow

Run this local quality gate before opening a PR (matches `pyproject.toml`):

```bash
pre-commit run --all-files
ruff check .
black --check .
flake8
mypy src tests
pytest
bandit -r src/equinox
safety check
```

## Performance Benchmark Harness

Use the built-in history-search benchmark to catch regressions in search/index behavior.

```bash
python scripts/benchmark_history_search.py --entries 5000 --runs 20
```

The benchmark prints JSON metrics (`min_ms`, `avg_ms`, `p95_ms`, `max_ms`) suitable for CI trend tracking.

## Safe Change Checklist

Before requesting review, confirm all items below:

- [ ] Validation boundary preserved: all untrusted input paths go through the `Validator` facade.
- [ ] Plugin permissions are least-privilege and explicit (no implicit capability expansion).
- [ ] Deny-by-default plugin policy still holds for newly introduced plugin behavior.
- [ ] Security-relevant actions emit audit logs with useful context and correlation IDs.
- [ ] Database schema updates are implemented only through `storage/migrations.py`.
- [ ] Migration upgrades from previous versions are covered by tests.
- [ ] History capture and search limits remain bounded (no unbounded body or regex work).
- [ ] Security regression tests are updated (validation, redaction, permissions, migrations).

## Architecture Decisions

- `docs/adr/ADR-0001-security-boundaries-and-extension-safety.md`

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
