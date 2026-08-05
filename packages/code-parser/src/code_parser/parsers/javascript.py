"""JavaScript parser — config-driven via _generic."""

from __future__ import annotations

from pathlib import Path

import tree_sitter

from code_parser.grammars import get_language
from code_parser.parsers._base import LanguageParser
from code_parser.parsers._config import LanguageConfig
from code_parser.parsers._generic import generic_walk
from code_parser.tree import SourceNode

JAVASCRIPT_CONFIG = LanguageConfig(
    grammar_name="javascript",
    language="javascript",
    class_types=frozenset({"class_declaration"}),
    function_types=frozenset({"function_declaration", "arrow_function", "function_expression"}),
    method_types=frozenset({"method_definition"}),
    import_types=frozenset({"import_statement"}),
    export_types=frozenset({"export_statement"}),
    call_types=frozenset({"call_expression"}),
    name_field="name",
    body_field="body",
    call_function_field="function",
    call_member_node_types=frozenset({"member_expression"}),
    call_member_field="property",
    function_boundary_types=frozenset(
        {
            "function_declaration",
            "arrow_function",
            "function_expression",
            "method_definition",
        }
    ),
)


class JavaScriptParser(LanguageParser):
    name = "javascript"
    file_extensions = (".js", ".jsx", ".mjs", ".cjs")

    @property
    def grammar(self) -> tree_sitter.Language:
        return get_language("javascript")

    def parse(self, path: Path, source: bytes, *, package: str | None = None) -> SourceNode:
        return generic_walk(
            JAVASCRIPT_CONFIG,
            path,
            source,
            package=package,
            language="javascript",
        )
