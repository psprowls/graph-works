"""Top-level parse_file / parse_bytes dispatch."""

from __future__ import annotations

from pathlib import Path

from code_graph_io.parser.errors import UnsupportedLanguageError
from code_graph_io.parser.parsers import EXTENSIONS, PARSERS
from code_graph_io.parser.tree import SourceNode


def parse_file(path: Path, *, package: str | None = None) -> SourceNode:
    """Read `path` from disk and parse using the parser registered for its extension."""
    parser = EXTENSIONS.get(path.suffix)
    if parser is None:
        raise UnsupportedLanguageError(
            f"No parser registered for extension {path.suffix!r}",
            path=path,
            extension=path.suffix,
        )
    source = path.read_bytes()
    return parser.parse(path, source, package=package)


def parse_bytes(
    source: bytes,
    *,
    path: Path,
    language: str | None = None,
    package: str | None = None,
) -> SourceNode:
    """Parse already-loaded `source` bytes. `language` overrides extension-based dispatch."""
    if language is not None:
        parser = PARSERS.get(language)
        if parser is None:
            raise UnsupportedLanguageError(
                f"No parser registered for language {language!r}",
                path=path,
                extension=None,
            )
    else:
        parser = EXTENSIONS.get(path.suffix)
        if parser is None:
            raise UnsupportedLanguageError(
                f"No parser registered for extension {path.suffix!r}",
                path=path,
                extension=path.suffix,
            )
    return parser.parse(path, source, package=package)
