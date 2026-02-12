# Equinox User Guide

## Introduction

Equinox is a local-first API testing tool that provides both command-line and graphical interfaces for testing HTTP APIs. All data is stored locally in SQLite, giving you full control over your data without requiring cloud services.

## Installation

```bash
cd equinox
pip install -e .
```

## Command Line Interface (CLI)

### Sending Requests

#### GET Request
```bash
equinox get https://api.example.com/users
```

#### POST Request with JSON
```bash
equinox post https://api.example.com/users --json '{"name": "John", "email": "john@example.com"}'
```

#### Request with Headers
```bash
equinox get https://api.example.com/protected \
  -H "Authorization: Bearer TOKEN" \
  -H "Accept: application/json"
```

#### Request with Query Parameters
```bash
equinox get https://api.example.com/search \
  -p "q=python" \
  -p "limit=10"
```

### Authentication

#### Bearer Token
```bash
equinox get https://api.example.com/protected --auth bearer:YOUR_TOKEN
```

#### Basic Authentication
```bash
equinox get https://api.example.com/protected --auth basic:username:password
```

#### API Key (Header)
```bash
equinox get https://api.example.com/data --auth apikey:header:X-API-Key:YOUR_KEY
```

#### API Key (Query Parameter)
```bash
equinox get https://api.example.com/data --auth apikey:query:api_key:YOUR_KEY
```

### Collections

Collections help organize your requests.

#### Create a Collection
```bash
equinox collection create "My API" --description "Testing my API"
```

#### List Collections
```bash
equinox collection list
```

#### Save a Request
```bash
equinox get https://api.example.com/users --save "Get all users"
```

#### View Requests in Collection
```bash
equinox collection requests 1
```

### Environments

Environments store variables that can be used across requests.

#### Create Environment
```bash
equinox env create "Development" \
  -v "BASE_URL=http://localhost:3000" \
  -v "API_KEY=dev_key_123"
```

#### List Environments
```bash
equinox env list
```

#### Activate Environment
```bash
equinox env activate 1
```

#### Using Variables
Use `{{VARIABLE_NAME}}` in your requests:
```bash
equinox get "{{BASE_URL}}/api/users" -H "X-API-Key: {{API_KEY}}"
```

### History

View your request history:
```bash
equinox history --limit 20
```

## Graphical User Interface (GUI)

### Launching the GUI
```bash
equinox gui
```

### Main Interface

The GUI consists of four main areas:

1. **Collections Panel** (left) - Organize and manage request collections
2. **Request Builder** (top right) - Build and send HTTP requests
3. **Response Viewer** (bottom right) - View response details
4. **History Panel** (left tab) - Browse request history

### Making a Request

1. Select HTTP method from dropdown (GET, POST, etc.)
2. Enter the URL
3. Add headers in the Headers tab (key-value pairs)
4. Add query parameters in the Params tab
5. Add request body in the Body tab (for POST/PUT/PATCH)
6. Click "Send"

### Saving Requests

1. Build your request
2. Click "Save Request"
3. Enter a name for the request
4. Request is saved to the default collection

### Managing Collections

1. Click "New Collection" in the Collections panel
2. Enter a collection name
3. Double-click a request to load it
4. Right-click to delete collections or requests

### Viewing History

1. Switch to the History tab
2. Double-click an entry to reload it
3. Click "Clear All" to delete history

## Advanced Features

### Request Body from File

```bash
equinox post https://api.example.com/upload --data @path/to/file.json
```

### Disable SSL Verification

```bash
equinox get https://self-signed.example.com --no-verify
```

### Custom Timeout

```bash
equinox get https://slow-api.example.com --timeout 60
```

### Debug Mode

```bash
equinox --debug get https://api.example.com/data
```

## Tips and Tricks

1. **Save frequently used requests** to collections for quick access
2. **Use environments** to switch between development, staging, and production
3. **View history** to replay previous requests
4. **Use curl export** to share requests with others (coming soon)
5. **Install plugins** to extend functionality

## Troubleshooting

### Database Location

Equinox stores data in `~/.equinox/equinox.db`

### Resetting the Database

```bash
rm ~/.equinox/equinox.db
```

### Common Errors

**"Connection refused"** - Check if the API server is running

**"SSL verification failed"** - Use `--no-verify` for self-signed certificates (development only)

**"Timeout"** - Increase timeout with `--timeout` flag

## Next Steps

- Read the [Plugin Development Guide](plugin-development.md)
- Check out [example collections](../examples/)
- Join our community and contribute
