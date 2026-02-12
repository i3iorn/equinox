# Equinox

A local-first API testing tool with both CLI and GUI interfaces - your lightweight, customizable alternative to Postman.

## Features

- **Local-First**: All data stored locally in SQLite - no cloud required
- **CLI & GUI**: Use command-line for automation or GUI for visual exploration
- **Collections**: Organize requests into collections
- **Environments**: Manage multiple environments with variables
- **Authentication**: Support for OAuth2, Bearer Token, API Key, Basic Auth
- **History**: Track all requests and responses
- **Plugin System**: Extend functionality with custom plugins
- **Response Visualization**: View JSON, XML, HTML responses with syntax highlighting
- **Import/Export**: Share collections and environments

## Installation

```bash
pip install -e .
```

## Quick Start

### CLI Usage

```bash
# Send a GET request
equinox get https://api.example.com/users

# Send a POST request with JSON body
equinox post https://api.example.com/users --json '{"name": "John"}'

# Use authentication
equinox get https://api.example.com/protected --auth bearer:YOUR_TOKEN

# Save request to collection
equinox save my-request --url https://api.example.com/users --method GET

# View history
equinox history
```

### GUI Usage

```bash
# Launch GUI
equinox gui
```

## Project Structure

```
equinox/
├── src/equinox/
│   ├── core/          # Core HTTP client and request handling
│   ├── auth/          # Authentication strategies
│   ├── storage/       # SQLite database and persistence
│   ├── cli/           # Command-line interface
│   ├── gui/           # PyQt6 GUI application
│   └── plugins/       # Plugin system and built-in plugins
├── tests/             # Test suite
├── docs/              # Documentation
├── examples/          # Example collections and plugins
└── plugins/           # User-installed plugins

```

## Development

```bash
# Install development dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Run with debug logging
equinox --debug gui
```

## Plugin Development

See [docs/plugin-development.md](docs/plugin-development.md) for creating custom plugins.

## License

MIT License
