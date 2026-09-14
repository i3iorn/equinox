"""The script sandbox must not hand out modules it did not allowlist.

The import allowlist governs which modules a script may *import*, but several
stdlib modules keep a plain reference to a dangerous one. Before the
_SafeModule proxy, an allowlisted import was enough to reach the real thing:

    import uuid
    uuid.os.system("...")          # arbitrary command execution
    uuid.os.environ["SECRET"]      # environment exfiltration
    codecs.builtins.eval("...")    # the blocked builtins, handed back
    codecs.open(path).read()       # arbitrary file read, despite open() blocked
    collections._sys.modules       # every loaded module

Script output flows back to the caller through `env`, so a read was also an
exfiltration path, and importing a shared collection can carry scripts
(see importers/postman.py), so the source need not be the user's own.

`test_no_allowlisted_module_leaks_any_module` is the one that matters: naming
the five known vectors would not stop a stdlib change from adding a sixth.
"""

from __future__ import annotations

import importlib
import types

import pytest

from equinox.core.scripts.models import ALLOWED_MODULES
from equinox.core.scripts.sandbox import _SafeModule, _safe_import

# (label, script) — each must fail rather than reach the wrapped module.
_ESCAPES = [
    ("uuid.os", "import uuid\nenv['r'] = uuid.os.getcwd()"),
    ("uuid.os.environ", "import uuid\nenv['r'] = str(uuid.os.environ)"),
    ("codecs.builtins", "import codecs\nenv['r'] = str(codecs.builtins.eval('1+1'))"),
    ("codecs.sys", "import codecs\nenv['r'] = str(codecs.sys.executable)"),
    ("random._os", "import random\nenv['r'] = random._os.getcwd()"),
    ("collections._sys", "import collections\nenv['r'] = str(collections._sys.modules)"),
    ("pprint._sys", "import pprint\nenv['r'] = str(pprint._sys.executable)"),
]


@pytest.mark.parametrize(("label", "script"), _ESCAPES, ids=[e[0] for e in _ESCAPES])
def test_known_module_escapes_are_blocked(label, script):
    from equinox.core.scripts import ScriptRunner

    result = ScriptRunner.run_pre(script, {}, {})
    assert not result.ok, f"{label} escaped the sandbox: {result.env_changes}"
    assert "not allowed in scripts" in (result.error or "")


def test_codecs_open_is_blocked(tmp_path):
    """open() is stripped from builtins, so codecs must not offer its own."""
    from equinox.core.scripts import ScriptRunner

    secret = tmp_path / "secret.txt"
    secret.write_text("TOP-SECRET")

    script = f"import codecs\nenv['r'] = codecs.open(r'{secret}', 'r', 'utf-8').read()"
    result = ScriptRunner.run_pre(script, {}, {})

    assert not result.ok
    assert "not available in scripts" in (result.error or "")
    assert "TOP-SECRET" not in str(result.env_changes)


def test_no_allowlisted_module_leaks_any_module():
    """Exhaustive: no attribute of any allowlisted module yields a module.

    The exception is a module that is itself allowlisted (``urllib.parse``),
    which comes back wrapped rather than raw.
    """
    offenders = []
    for name in sorted(ALLOWED_MODULES):
        try:
            module = importlib.import_module(name)
        except ImportError:  # pragma: no cover - allowlist is stdlib only
            continue
        proxy = _SafeModule(module)
        for attr in dir(module):
            try:
                value = getattr(proxy, attr)
            except AttributeError:
                continue  # refused, which is the point
            if isinstance(value, types.ModuleType):
                offenders.append(f"{name}.{attr} -> raw module {value.__name__}")
            elif isinstance(value, _SafeModule):
                inner = object.__getattribute__(value, "_wrapped").__name__
                if inner not in ALLOWED_MODULES:
                    offenders.append(f"{name}.{attr} -> {inner} (not allowlisted)")

    assert not offenders, "sandbox leaks modules: " + "; ".join(offenders)


def test_allowlisted_submodule_is_still_reachable_but_wrapped():
    """urllib.parse is allowlisted, so it stays usable — as a proxy."""
    urllib_proxy = _safe_import("urllib", fromlist=["parse"])
    parse = urllib_proxy.parse
    assert isinstance(parse, _SafeModule)
    assert parse.quote("a b") == "a%20b"


def test_sandboxed_modules_are_read_only():
    """Rebinding an attribute would let one script poison another's view.

    Deliberately wraps a throwaway module rather than a real one. An earlier
    draft used `json`, and when run against the pre-fix sandbox the write
    landed on the actual module — `json.dumps` became None and took pytest's
    cache provider down with it. A regression here should fail the test, not
    corrupt the interpreter running it.
    """
    scratch = types.ModuleType("scratch_module")
    scratch.value = "original"  # type: ignore[attr-defined]
    proxy = _SafeModule(scratch)

    assert proxy.value == "original"
    with pytest.raises(AttributeError):
        proxy.value = "overwritten"
    with pytest.raises(AttributeError):
        del proxy.value
    assert scratch.value == "original"  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("label", "script"),
    [
        ("json", "import json\nenv['r'] = json.dumps({'a': 1})"),
        ("from-import", "from json import dumps\nenv['r'] = dumps({'a': 1})"),
        ("urllib.parse", "import urllib.parse\nenv['r'] = urllib.parse.quote('a b')"),
        ("hashlib", "import hashlib\nenv['r'] = hashlib.sha256(b'x').hexdigest()"),
        ("datetime", "import datetime\nenv['r'] = datetime.datetime(2020, 1, 1).isoformat()"),
        ("uuid", "import uuid\nenv['r'] = str(uuid.uuid4())"),
        ("collections", "import collections\nenv['r'] = str(collections.Counter('aab')['a'])"),
    ],
)
def test_ordinary_script_usage_still_works(label, script):
    """The proxy must not cost scripts their legitimate stdlib access."""
    from equinox.core.scripts import ScriptRunner

    result = ScriptRunner.run_pre(script, {}, {})
    assert result.ok, f"{label} broke: {result.error}"
    assert result.env_changes.get("r")
