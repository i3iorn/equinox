import json

from equinox.core.exceptions import SecurityError


def safe_json_dumps(obj, *, max_len=None, indent=None, ensure_ascii=True, sort_keys=False):
    s = json.dumps(obj, indent=indent, ensure_ascii=ensure_ascii, sort_keys=sort_keys)
    if max_len is not None and len(s) > max_len:
        raise SecurityError(f"JSON serialization exceeds {max_len} bytes")
    return s
