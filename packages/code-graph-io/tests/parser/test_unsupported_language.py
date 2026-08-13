from pathlib import Path

import pytest
from code_graph_io.parser import UnsupportedLanguageError, parse_bytes, parse_file


def test_parse_file_unknown_extension():
    with pytest.raises(UnsupportedLanguageError) as exc_info:
        parse_file(Path("foo.cobol"))
    assert isinstance(exc_info.value, ValueError)
    assert exc_info.value.extension == ".cobol"
    assert exc_info.value.path == Path("foo.cobol")


def test_parse_bytes_unknown_extension():
    with pytest.raises(UnsupportedLanguageError) as exc_info:
        parse_bytes(b"x", path=Path("foo.cobol"))
    assert exc_info.value.extension == ".cobol"


def test_parse_bytes_unknown_language_override():
    with pytest.raises(UnsupportedLanguageError):
        parse_bytes(b"x", path=Path("foo.py"), language="cobol")
