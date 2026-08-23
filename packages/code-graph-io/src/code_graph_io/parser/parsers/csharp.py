"""C# parser — config-driven via _generic. Mirrors JavaScriptParser's shape."""

from __future__ import annotations

from pathlib import Path

import tree_sitter

from code_graph_io.parser.grammars import get_language
from code_graph_io.parser.parsers._base import LanguageParser
from code_graph_io.parser.parsers._config import LanguageConfig
from code_graph_io.parser.parsers._generic import generic_walk
from code_graph_io.parser.tree import SourceNode

CSHARP_CONFIG = LanguageConfig(
    grammar_name="csharp",
    language="csharp",
    class_types=frozenset({"class_declaration", "struct_declaration", "record_declaration"}),
    function_types=frozenset(),  # C# has no free functions — methods only
    method_types=frozenset({"method_declaration", "constructor_declaration"}),
    type_types=frozenset({"interface_declaration", "enum_declaration"}),
    import_types=frozenset({"using_directive"}),
    import_module_node_types=frozenset({"qualified_name", "identifier"}),
    transparent_container_types=frozenset({"namespace_declaration"}),
    call_types=frozenset({"invocation_expression"}),
    name_field="name",
    body_field="body",
    call_function_field="function",
    call_member_node_types=frozenset({"member_access_expression"}),
    call_member_field="name",
    call_member_object_field="expression",
    call_unwrap_node_types=frozenset({"generic_name"}),
)


class CSharpParser(LanguageParser):
    name = "csharp"
    file_extensions = (".cs",)

    @property
    def grammar(self) -> tree_sitter.Language:
        return get_language("csharp")

    def parse(self, path: Path, source: bytes, *, package: str | None = None) -> SourceNode:
        return generic_walk(
            CSHARP_CONFIG,
            path,
            source,
            package=package,
            language="csharp",
        )
