"""TypeScript parser — extends the JavaScript LanguageConfig with TS-specific node types."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import tree_sitter

from code_parser.grammars import get_language
from code_parser.parsers._base import LanguageParser
from code_parser.parsers._generic import generic_walk
from code_parser.parsers.javascript import JAVASCRIPT_CONFIG
from code_parser.tree import SourceNode

TYPESCRIPT_CONFIG = replace(
    JAVASCRIPT_CONFIG,
    grammar_name="typescript",
    language="typescript",
    class_types=JAVASCRIPT_CONFIG.class_types | {"abstract_class_declaration"},
    function_types=JAVASCRIPT_CONFIG.function_types | {"function_signature"},
    method_types=JAVASCRIPT_CONFIG.method_types | {"method_signature", "abstract_method_signature"},
    type_types=frozenset({"interface_declaration", "type_alias_declaration", "enum_declaration"}),
)

# `.tsx` carries JSX, which the plain `typescript` grammar cannot parse (it
# produces an error-laden tree that drops most component definitions). The
# `tsx` grammar handles JSX; node types are otherwise identical, so the only
# difference is which grammar performs the parse. The logical `language` stays
# "typescript" so emitted nodes are unchanged for downstream consumers.
TSX_CONFIG = replace(TYPESCRIPT_CONFIG, grammar_name="tsx")


class TypeScriptParser(LanguageParser):
    name = "typescript"
    file_extensions = (".ts", ".tsx")

    @property
    def grammar(self) -> tree_sitter.Language:
        return get_language("typescript")

    def parse(self, path: Path, source: bytes, *, package: str | None = None) -> SourceNode:
        config = TSX_CONFIG if Path(path).suffix == ".tsx" else TYPESCRIPT_CONFIG
        return generic_walk(
            config,
            path,
            source,
            package=package,
            language="typescript",
        )
