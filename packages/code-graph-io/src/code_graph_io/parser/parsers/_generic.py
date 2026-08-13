"""Config-driven generic walker that turns a tree-sitter parse tree into a SourceNode tree."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import tree_sitter

from code_graph_io.parser.grammars import get_language
from code_graph_io.parser.parsers._config import LanguageConfig
from code_graph_io.parser.tree import Reference, SourceNode, Span


def _span(node: tree_sitter.Node) -> Span:
    sp = node.start_point
    ep = node.end_point
    return Span(
        start_byte=node.start_byte,
        end_byte=node.end_byte,
        start_line=sp[0] + 1,
        end_line=ep[0] + 1,
        start_col=sp[1],
        end_col=ep[1],
    )


def _text(node: tree_sitter.Node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _resolve_name(node: tree_sitter.Node, source: bytes, config: LanguageConfig) -> str | None:
    n = node.child_by_field_name(config.name_field)
    if n is not None:
        return _text(n, source)
    for child in node.children:
        if child.type in config.name_fallback_child_types:
            return _text(child, source)
    return None


def _resolve_body(node: tree_sitter.Node, config: LanguageConfig) -> tree_sitter.Node | None:
    b = node.child_by_field_name(config.body_field)
    if b is not None:
        return b
    for child in node.children:
        if child.type in config.body_fallback_child_types:
            return child
    return None


def _collect_parse_errors(root: tree_sitter.Node) -> list[dict[str, int]]:
    errors: list[dict[str, int]] = []

    def visit(node: tree_sitter.Node) -> None:
        if node.is_error or node.type == "ERROR":
            errors.append(
                {
                    "start_byte": node.start_byte,
                    "end_byte": node.end_byte,
                    "start_line": node.start_point[0] + 1,
                    "end_line": node.end_point[0] + 1,
                }
            )
        for child in node.children:
            visit(child)

    visit(root)
    return errors


def _extract_call_target(
    call_node: tree_sitter.Node, source: bytes, config: LanguageConfig
) -> tuple[str, dict[str, Any]]:
    """Return (target_name, attrs) for a call expression."""
    fn = call_node.child_by_field_name(config.call_function_field)
    if fn is None:
        return ("<unknown>", {})
    if fn.type in config.call_member_node_types:
        attrs: dict[str, Any] = {"is_member": True}
        obj = fn.child_by_field_name(config.call_member_object_field)
        if obj is not None:
            attrs["receiver"] = _text(obj, source)
        prop = fn.child_by_field_name(config.call_member_field)
        if prop is not None:
            return (_text(prop, source), attrs)
        return (_text(fn, source), attrs)
    return (_text(fn, source), {"is_member": False})


def _extract_calls(body: tree_sitter.Node, source: bytes, config: LanguageConfig) -> list[Reference]:
    calls: list[Reference] = []

    def visit(node: tree_sitter.Node, *, inside_nested_fn: bool) -> None:
        if not inside_nested_fn and node.type in config.call_types:
            name, attrs = _extract_call_target(node, source, config)
            calls.append(
                Reference(
                    kind="call",
                    target_name=name,
                    target_module=None,
                    site=_span(node),
                    attrs=attrs,
                )
            )
        # Stop descending into nested function bodies — their calls belong to them.
        descend_marks_nested = node.type in config.function_boundary_types
        for child in node.children:
            visit(
                child,
                inside_nested_fn=inside_nested_fn or (descend_marks_nested and child is not node),
            )
        # The above check is overly conservative; walk the body of nested functions
        # via their own SourceNode instead. Practical effect: top-level walk skips
        # nested function bodies' calls.

    for child in body.children:
        visit(child, inside_nested_fn=False)
    return calls


def _build_function_node(
    node: tree_sitter.Node,
    source: bytes,
    path: Path,
    language: str,
    package: str | None,
    config: LanguageConfig,
    kind: str,
) -> SourceNode:
    name = _resolve_name(node, source, config)
    body = _resolve_body(node, config)
    fn = SourceNode(
        kind=kind,
        name=name,
        span=_span(node),
        path=path,
        language=language,
        package=package,
    )
    if kind == "method" and name in ("constructor",):
        fn.attrs["is_constructor"] = True
    if body is not None:
        fn.refs.extend(_extract_calls(body, source, config))
        # Nested classes/functions inside the body become children.
        fn.children.extend(_walk_container(body, source, path, language, package, config))
    return fn


_TS_KIND_MAP: dict[str, str] = {
    "interface_declaration": "interface",
    "type_alias_declaration": "type_alias",
    "enum_declaration": "enum",
}


def _ts_kind_for(node_type: str) -> str:
    return _TS_KIND_MAP.get(node_type, node_type)


def _build_type_node(
    node: tree_sitter.Node,
    source: bytes,
    path: Path,
    language: str,
    package: str | None,
    config: LanguageConfig,
) -> SourceNode:
    name = _resolve_name(node, source, config) or "<anonymous>"
    return SourceNode(
        kind="type",
        name=name,
        span=_span(node),
        path=path,
        language=language,
        package=package,
        attrs={"ts_kind": _ts_kind_for(node.type)},
    )


def _build_class_node(
    node: tree_sitter.Node,
    source: bytes,
    path: Path,
    language: str,
    package: str | None,
    config: LanguageConfig,
) -> SourceNode:
    name = _resolve_name(node, source, config)
    body = _resolve_body(node, config)
    cls = SourceNode(
        kind="class",
        name=name,
        span=_span(node),
        path=path,
        language=language,
        package=package,
    )
    if body is not None:
        for child in body.children:
            if child.type in config.method_types:
                cls.children.append(
                    _build_function_node(
                        child,
                        source,
                        path,
                        language,
                        package,
                        config,
                        kind="method",
                    )
                )
            elif child.type in config.class_types:
                cls.children.append(
                    _build_class_node(
                        child,
                        source,
                        path,
                        language,
                        package,
                        config,
                    )
                )
            elif child.type in config.function_types:
                cls.children.append(
                    _build_function_node(
                        child,
                        source,
                        path,
                        language,
                        package,
                        config,
                        kind="function",
                    )
                )
    return cls


def _arrow_consts_in(
    decl_node: tree_sitter.Node,
    source: bytes,
    path: Path,
    language: str,
    package: str | None,
    config: LanguageConfig,
) -> list[SourceNode]:
    """Build function nodes for `const NAME = <arrow|fn_expr>` declarators.

    Accepts a `lexical_declaration` or `variable_declaration` node and returns
    one SourceNode per variable_declarator whose value is an arrow_function or
    function_expression.  The emitted node's name comes from the declarator's
    `name` field; its span covers the full declaration so start_line lands on
    the `const` line.
    """
    out: list[SourceNode] = []
    for child in decl_node.children:
        if child.type != "variable_declarator":
            continue
        value = child.child_by_field_name("value")
        if value is None or value.type not in config.function_types:
            continue
        name_node = child.child_by_field_name("name")
        if name_node is None:
            continue
        name = _text(name_node, source)
        # Build via the arrow/fn-expr node so body/calls/nested containers are
        # extracted correctly, then patch the name and span.
        fn = _build_function_node(value, source, path, language, package, config, kind="function")
        fn.name = name
        fn.span = _span(decl_node)
        out.append(fn)
    return out


def _walk_container(
    node: tree_sitter.Node,
    source: bytes,
    path: Path,
    language: str,
    package: str | None,
    config: LanguageConfig,
) -> list[SourceNode]:
    """Yield direct-child symbol nodes (classes/functions) under `node`.

    Also looks one level inside export_statement nodes so that
    `export function foo(){}` produces a function child (plus an export ref
    from _extract_exports).  Handles `const NAME = () => {}` and
    `export const NAME = () => {}` via _arrow_consts_in.
    """
    out: list[SourceNode] = []
    for child in node.children:
        if child.type in config.class_types:
            out.append(_build_class_node(child, source, path, language, package, config))
        elif child.type in config.function_types:
            out.append(_build_function_node(child, source, path, language, package, config, kind="function"))
        elif child.type in config.type_types:
            out.append(_build_type_node(child, source, path, language, package, config))
        elif child.type in ("lexical_declaration", "variable_declaration"):
            out.extend(_arrow_consts_in(child, source, path, language, package, config))
        elif child.type in config.export_types:
            # Peek inside: `export function foo(){}` or `export class Foo{}`
            # or `export const NAME = () => {}` or `export interface Foo {}`
            for inner in child.children:
                if inner.type in config.class_types:
                    out.append(_build_class_node(inner, source, path, language, package, config))
                elif inner.type in config.function_types:
                    out.append(
                        _build_function_node(
                            inner,
                            source,
                            path,
                            language,
                            package,
                            config,
                            kind="function",
                        )
                    )
                elif inner.type in config.type_types:
                    out.append(_build_type_node(inner, source, path, language, package, config))
                elif inner.type in ("lexical_declaration", "variable_declaration"):
                    out.extend(_arrow_consts_in(inner, source, path, language, package, config))
    return out


def _extract_imports(file_root: tree_sitter.Node, source: bytes, config: LanguageConfig) -> list[Reference]:
    """Pull import edges off the top level of a file."""
    refs: list[Reference] = []
    for child in file_root.children:
        if child.type not in config.import_types:
            continue
        # Module path is typically the first 'string' descendant.
        module = None
        for desc in child.children:
            if desc.type == "string":
                module = _text(desc, source).strip("'\"")
                break
        # Imported names are 'identifier' nodes inside named/namespace clauses.
        # For v1 we collect each top-level identifier as a separate import.
        seen_names: list[str] = []

        # Invoked immediately below, inside this same iteration, so the
        # loop variables it closes over cannot be rebound before the call.
        def walk_names(n: tree_sitter.Node) -> None:
            if n.type == "identifier":
                seen_names.append(_text(n, source))  # noqa: B023
                return
            for c in n.children:
                walk_names(c)

        walk_names(child)
        # Filter the module-string identifier out (rare): handled above.
        # If no names found, emit a single bare import.
        names_to_emit = seen_names or ["<module>"]
        for name in names_to_emit:
            refs.append(
                Reference(
                    kind="import",
                    target_name=name,
                    target_module=module,
                    site=_span(child),
                    attrs={},
                )
            )
    return refs


def _extract_exports(file_root: tree_sitter.Node, source: bytes, config: LanguageConfig) -> list[Reference]:
    refs: list[Reference] = []
    for child in file_root.children:
        if child.type not in config.export_types:
            continue
        # Detect `export type { ... }` — the `type` keyword is a direct child of
        # the export_statement and indicates that ALL re-exported names are types.
        # (TypeScript-only; JS config has no type_types so this flag is never set.)
        is_type_reexport = bool(config.type_types) and any(
            c.type == "type" and c.text == b"type" for c in child.children
        )
        # Identifier-name(s) and optional symbol_kind on the export.
        # Each entry is (name, symbol_kind_or_None).
        named: list[tuple[str, str | None]] = []
        has_declaration = False
        for desc in child.children:
            if desc.type in ("lexical_declaration", "variable_declaration"):
                # `export const NAME = <anything>` — emit only the declarator
                # name(s); do NOT fall through to the broad walk() which leaks
                # param/body identifiers for arrow/fn_expr values.
                has_declaration = True
                for decl in desc.children:
                    if decl.type != "variable_declarator":
                        continue
                    name_node = decl.child_by_field_name("name")
                    if name_node is None:
                        continue
                    value = decl.child_by_field_name("value")
                    # Only stamp symbol_kind=function when the value is actually
                    # a function/arrow; leave plain const exports without a kind.
                    if value is not None and value.type in config.function_types:
                        named.append((_text(name_node, source), "function"))
                    else:
                        named.append((_text(name_node, source), None))
            elif desc.type == "identifier":
                named.append((_text(desc, source), None))
            elif desc.type in config.class_types:
                n = _resolve_name(desc, source, config)
                if n:
                    has_declaration = True
                    named.append((n, "class"))
            elif desc.type in config.function_types:
                n = _resolve_name(desc, source, config)
                if n:
                    has_declaration = True
                    named.append((n, "function"))
            elif desc.type in config.type_types:
                n = _resolve_name(desc, source, config)
                if n:
                    has_declaration = True
                    named.append((n, "type"))
        if not named and not has_declaration:
            # Look for export-clause -> { name, name, ... }
            # For `export type { X }`, stamp symbol_kind="type" on each name since
            # the `type` keyword explicitly identifies these as type re-exports.
            # For plain `export { x }`, symbol kind is unknown at parse time (None).
            reexport_kind: str | None = "type" if is_type_reexport else None

            # Invoked immediately below, inside this same iteration, so the
            # loop variables it closes over cannot be rebound before the call.
            def walk(n: tree_sitter.Node) -> None:
                if n.type == "identifier":
                    named.append((_text(n, source), reexport_kind))  # noqa: B023
                for c in n.children:
                    walk(c)

            walk(child)
        for name, symbol_kind in named:
            attrs: dict[str, Any] = {}
            if symbol_kind is not None:
                attrs["symbol_kind"] = symbol_kind
            refs.append(
                Reference(
                    kind="export",
                    target_name=name,
                    target_module=None,
                    site=_span(child),
                    attrs=attrs,
                )
            )
    return refs


def generic_walk(
    config: LanguageConfig,
    path: Path,
    source: bytes,
    package: str | None,
    language: str,
) -> SourceNode:
    """Parse `source` with `config.grammar_name` and walk into a SourceNode tree."""
    grammar = get_language(config.grammar_name)
    parser = tree_sitter.Parser(grammar)
    tree = parser.parse(source)
    root = tree.root_node

    file_node = SourceNode(
        kind="file",
        name=None,
        span=_span(root),
        path=path,
        language=language,
        package=package,
    )

    parse_errors = _collect_parse_errors(root)
    if parse_errors:
        file_node.attrs["parse_errors"] = parse_errors

    file_node.children.extend(_walk_container(root, source, path, language, package, config))
    file_node.refs.extend(_extract_imports(root, source, config))
    file_node.refs.extend(_extract_exports(root, source, config))
    return file_node
