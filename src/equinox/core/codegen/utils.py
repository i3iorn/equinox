from equinox.core import urls


def _auth_type_name(auth) -> str:
    return type(auth).__name__ if auth else ""


_REDACTED_TOKEN = "<YOUR_TOKEN>"
_REDACTED_USER = "<YOUR_USERNAME>"
_REDACTED_PASS = "<YOUR_PASSWORD>"
_REDACTED_KEY = "<YOUR_API_KEY>"


def _escape_single_quoted(s: str) -> str:
    return s.replace("\\", "\\\\").replace("'", "\\\\'")


def _escape_go_string(s: str) -> str:
    return (
        s.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )


def _build_url_with_params(url: str, params: dict) -> str:
    return urls.append_query_params(url, params, merge_existing=False)


_escape_ruby_single = _escape_single_quoted
_escape_php_single = _escape_single_quoted
