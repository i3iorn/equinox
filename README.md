# Equinox

A secure, local-first API testing tool with both CLI and GUI interfaces.

## Table of Contents

- [Overview](#overview)
- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [Development](#development)
- [Examples](#examples)
- [Contributing](#contributing)
- [Security](#security)
- [License](#license)

## Overview

Equinox helps you test APIs locally without depending on cloud services. It supports interactive GUI workflows, CLI automation, reusable collections, and environment-based configuration.

## Features

- Local-first data storage
- CLI and GUI interfaces
- Request collections and environments
- Request/response history
- Authentication support (OAuth2, Bearer Token, API Key, Basic Auth)
- Plugin extension system
- Security-focused request handling and validation

## Requirements

- Python 3.9+

## Installation

```bash
git clone https://github.com/i3iorn/equinox.git
cd equinox

# Install runtime dependencies
pip install -e .

# Install development dependencies
pip install -e ".[dev]"
```

## Quick Start

### CLI

```bash
# Send a GET request
equinox get https://api.example.com/users

# Send a POST request with JSON body
equinox post https://api.example.com/users --json '{"name":"John"}'

# Launch GUI
equinox gui
```

### Shell Completion

```bash
# Bash
eval "$(_EQUINOX_COMPLETE=bash_source equinox)"

# Zsh
eval "$(_EQUINOX_COMPLETE=zsh_source equinox)"

# Fish
_EQUINOX_COMPLETE=fish_source equinox | source

# PowerShell
_EQUINOX_COMPLETE=powershell_source equinox | Invoke-Expression
```

## Development

```bash
# Install development dependencies
pip install -e ".[dev]"

# Install pre-commit hooks
pre-commit install

# Run tests
pytest

# Run security checks
bandit -r src/ -ll
safety check

# Run all checks
pre-commit run --all-files
```

## Examples

See the [`examples/`](examples) directory for sample collections, environments, plugins, and scripts.

## Contributing

Contributions are welcome. Please open an issue for discussion or submit a pull request with a clear description of your changes.

## Security

If you discover a security vulnerability, please open a private security advisory on GitHub.

## License

MIT License. See [LICENSE](LICENSE).
