"""Declarative LanguageConfig for the config-driven generic walker."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class LanguageConfig:
    """Per-language description consumed by `parsers/_generic.py`.

    Lifted in spirit from graphify-extraction's LanguageConfig: a flat,
    declarative description of which AST node types matter and which fields to
    pluck names and bodies from. Languages whose AST shape doesn't fit this
    config (Python, today) write a custom walker instead of using this.
    """

    grammar_name: str  # 'python' | 'javascript' | 'typescript'
    language: str  # logical language name on emitted nodes

    class_types: frozenset[str] = frozenset()
    function_types: frozenset[str] = frozenset()
    method_types: frozenset[str] = frozenset()
    type_types: frozenset[str] = frozenset()
    """Node types that declare a type-like symbol (interface / type alias / enum)."""
    import_types: frozenset[str] = frozenset()
    export_types: frozenset[str] = frozenset()
    call_types: frozenset[str] = frozenset()

    name_field: str = "name"
    body_field: str = "body"

    # Fall-back child-type lookups when the field-based lookup misses.
    name_fallback_child_types: tuple[str, ...] = ()
    body_fallback_child_types: tuple[str, ...] = ()

    # Call-name extraction
    call_function_field: str = "function"
    call_member_node_types: frozenset[str] = frozenset()
    call_member_field: str = "property"
    call_member_object_field: str = "object"

    # Stop recursion at these node types (avoid descending into nested scopes
    # when looking for symbols at one nesting level).
    function_boundary_types: frozenset[str] = frozenset()

    transparent_container_types: frozenset[str] = frozenset()
    """Containers that hold declarations but emit no SourceNode of their own.

    C#'s `namespace_declaration` is the motivating case: symbols inside it belong
    to the file, not to a namespace node. Walked through in both directions --
    `_walk_container` descends for symbols, `_extract_imports` for import refs.
    """

    import_module_node_types: frozenset[str] = frozenset()
    """Child node types of an import directive that name the imported module.

    When set, the FIRST matching child that is not the directive's `name`-field
    child supplies the whole import target, replacing the identifier walk. C#
    needs this because a `using` has no string literal: `using System.Text.Json;`
    is one `qualified_name`, which the identifier walk shreds into three refs.
    """

    call_unwrap_node_types: frozenset[str] = frozenset()
    """Wrapper nodes to descend through when naming a call target.

    C#'s `Foo<int>()` puts a `generic_name` in the invocation's `function` field;
    its text includes the type arguments, so the target never matches a symbol
    named `Foo`.
    """

    # Per-language attribute extractors are wired in `_generic.py`; this
    # struct stays declarative so it can be inspected/diffed in tests.
    extra_attrs: tuple[str, ...] = field(default_factory=tuple)
