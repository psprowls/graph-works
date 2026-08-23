"""Per-language parsers and the registry."""

from __future__ import annotations

from code_graph_io.parser.parsers._base import LanguageParser
from code_graph_io.parser.parsers._config import LanguageConfig
from code_graph_io.parser.parsers.csharp import CSharpParser
from code_graph_io.parser.parsers.javascript import JavaScriptParser
from code_graph_io.parser.parsers.python import PythonParser
from code_graph_io.parser.parsers.typescript import TypeScriptParser

PARSERS: dict[str, LanguageParser] = {
    "python": PythonParser(),
    "javascript": JavaScriptParser(),
    "typescript": TypeScriptParser(),
    "csharp": CSharpParser(),
}

EXTENSIONS: dict[str, LanguageParser] = {ext: parser for parser in PARSERS.values() for ext in parser.file_extensions}

__all__ = [
    "EXTENSIONS",
    "PARSERS",
    "CSharpParser",
    "JavaScriptParser",
    "LanguageConfig",
    "LanguageParser",
    "PythonParser",
    "TypeScriptParser",
]
