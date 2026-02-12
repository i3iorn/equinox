# Getting Started with Equinox

Equinox is a local-first API testing tool with both CLI and GUI interfaces. This guide will help you get started.

## Installation

```bash
cd equinox
pip install -e .
```

## Quick Start

### Using the CLI

#### Send a simple GET request

```bash
equinox get https://api.github.com/users/octocat
```

#### Send a POST request with JSON

```bash
equinox post https://httpbin.org/post --json '{"name": "John", "age": 30}'
```

#### Add headers

```bash
equinox get https://api.example.com/protected \
  --header "Authorization: Bearer YOUR_TOKEN" \
  --header "Content-Type: application/json"
```

#### Add query parameters

```bash
equinox get https://api.example.com/search \
  --param "q=python" \
  --param "limit=10"
```

#### Use authentication

```bash
# Bearer token
equinox get https://api.example.com/protected --auth "bearer:YOUR_TOKEN"

# Basic auth
equinox get https://api.example.com/protected --auth "basic:username:password"

# API key (header)
equinox get https://api.example.com/protected --auth "apikey:header:X-API-Key:YOUR_KEY"

# API key (query param)
equinox get https://api.example.com/protected --auth "apikey:query:api_key:YOUR_KEY"
```

#### Save requests to collections

```bash
# Save a request
equinox get https://api.github.com/users/octocat --save "Get GitHub User"

# Create a collection
equinox collection create "GitHub API" --description "GitHub API requests"

# List collections
equinox collection list

# List requests in a collection
equinox collection requests 1
```

#### View history

```bash
# View recent requests
equinox history

# Limit number of entries
equinox history --limit 10
```

#### Manage environments

```bash
# Create an environment
equinox env create "development" \
  --var "BASE_URL=https://api.dev.example.com" \
  --var "API_KEY=dev-key-123" \
  --description "Development environment"

# List environments
equinox env list

# Activate an environment
equinox env activate 1
```

### Using the GUI

Launch the GUI application:

```bash
equinox gui
```

The GUI provides:

- **Request Builder**: Build and send HTTP requests with a visual interface
- **Response Viewer**: View responses with syntax highlighting
- **Collections**: Organize requests into collections
- **History**: View and replay previous requests
- **Environments**: Manage environment variables

## Features

### Local-First

All data is stored locally in SQLite database at `~/.equinox/equinox.db`. No cloud services required.

### Collections

Group related requests together:

```bash
equinox collection create "My API"
equinox get https://api.example.com/users --save "List Users"
```

### Environments

Use variables in requests:

1. Create an environment with variables
2. Activate the environment
3. Use `{{VARIABLE_NAME}}` in URLs, headers, or body

Example:

```bash
equinox env create "production" --var "BASE_URL=https://api.example.com"
equinox env activate 1
equinox get "{{BASE_URL}}/users"
```

### History

All requests are automatically saved to history. View them with:

```bash
equinox history
```

In the GUI, click on a history entry to replay it.

## Next Steps

- [Plugin Development Guide](plugin-development.md) - Create custom plugins
- [API Reference](api-reference.md) - Detailed API documentation
- [Examples](../examples/) - Example collections and plugins
