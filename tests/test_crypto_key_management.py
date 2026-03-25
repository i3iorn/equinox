import tempfile
from pathlib import Path

from equinox.core import crypto


def test_get_or_create_raw_key_roundtrip(tmp_path: Path):
    key_path = tmp_path / "test.key"
    # create key
    key = crypto.get_or_create_raw_key(key_path)
    assert isinstance(key, bytes)
    assert len(key) == 32

    # subsequent call reads same key
    key2 = crypto.get_or_create_raw_key(key_path)
    assert key == key2


def test_make_fernet_encrypts_and_decrypts(tmp_path: Path):
    key = crypto.get_or_create_raw_key(tmp_path / "another.key")
    f = crypto.make_fernet(key)
    plaintext = b'{"hello": "world"}'
    token = f.encrypt(plaintext)
    out = f.decrypt(token)
    assert out == plaintext

