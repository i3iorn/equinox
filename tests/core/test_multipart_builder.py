from pathlib import Path

import pytest

from equinox.core.exceptions import ValidationError
from equinox.core.io.multipart import build_multipart_files


def test_build_multipart_files_with_text_and_file(tmp_path: Path):
    # create a temporary file
    fpath = tmp_path / "sample.txt"
    fpath.write_text("hello world")

    multipart_data = [
        {"key": "field1", "type": "text", "value": "some text"},
        {"key": "file1", "type": "file", "value": str(fpath)},
    ]

    files, handles = build_multipart_files(multipart_data)
    assert files is not None
    # should have two entries
    assert len(files) == 2
    # one handle should be opened
    assert len(handles) == 1
    # close handles
    for h in handles:
        h.close()


def test_build_multipart_files_missing_file(tmp_path: Path):
    """Missing files fail fast instead of silently uploading an empty part."""
    multipart_data = [{"key": "file1", "type": "file", "value": str(tmp_path / "nofile.txt")}]
    with pytest.raises(ValidationError, match="file1"):
        build_multipart_files(multipart_data)
