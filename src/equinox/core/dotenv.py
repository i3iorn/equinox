"""Shared .env file parser used by both the CLI and GUI."""


def parse_dotenv(text: str) -> dict:
    """Parse a .env file text and return {key: value}.

    Handles ``KEY=VALUE``, ``export KEY=VALUE``, quoted values,
    comment lines (``#``), and blank lines.
    """
    result = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        if key:
            result[key] = value
    return result
