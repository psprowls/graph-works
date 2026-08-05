"""Python parser — custom walker because Python's import shapes, decorators,
and __all__ semantics don't fit the LanguageConfig dataclass cleanly.

Emits SourceNode/Reference of the same shape as the generic walker.
"""

from __future__ import annotations

from pathlib import Path

import tree_sitter

from code_parser.grammars import get_language
from code_parser.parsers._base import LanguageParser
from code_parser.tree import Reference, SourceNode, Span


def _span(node: tree_sitter.Node) -> Span:
    sp, ep = node.start_point, node.end_point
    return Span(node.start_byte, node.end_byte, sp[0] + 1, ep[0] + 1, sp[1], ep[1])


def _text(node: tree_sitter.Node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _collect_parse_errors(root: tree_sitter.Node) -> list[dict]:
    errors: list[dict] = []

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


def _name_of(node: tree_sitter.Node, source: bytes) -> str | None:
    n = node.child_by_field_name("name")
    return _text(n, source) if n is not None else None


def _body_of(node: tree_sitter.Node) -> tree_sitter.Node | None:
    return node.child_by_field_name("body")


def _calls_in(body: tree_sitter.Node, source: bytes) -> list[Reference]:
    out: list[Reference] = []

    def visit(node: tree_sitter.Node) -> None:
        if node.type == "function_definition":
            return  # don't descend into nested function bodies
        if node.type == "call":
            fn = node.child_by_field_name("function")
            if fn is not None:
                if fn.type == "attribute":
                    attr = fn.child_by_field_name("attribute")
                    name = _text(attr, source) if attr is not None else _text(fn, source)
                    attrs: dict = {"is_member": True}
                    obj = fn.child_by_field_name("object")
                    if obj is not None:
                        attrs["receiver"] = _text(obj, source)
                    out.append(
                        Reference(
                            kind="call",
                            target_name=name,
                            target_module=None,
                            site=_span(node),
                            attrs=attrs,
                        )
                    )
                else:
                    out.append(
                        Reference(
                            kind="call",
                            target_name=_text(fn, source),
                            target_module=None,
                            site=_span(node),
                            attrs={"is_member": False},
                        )
                    )
        for child in node.children:
            visit(child)

    for child in body.children:
        visit(child)
    return out


def _build_function(node: tree_sitter.Node, source: bytes, path: Path, package: str | None, kind: str) -> SourceNode:
    name = _name_of(node, source)
    fn = SourceNode(
        kind=kind,
        name=name,
        span=_span(node),
        path=path,
        language="python",
        package=package,
    )
    if kind == "method" and name == "__init__":
        fn.attrs["is_constructor"] = True
    body = _body_of(node)
    if body is not None:
        fn.refs.extend(_calls_in(body, source))
        # Nested classes/functions
        for child in body.children:
            inner_kind = (
                "class"
                if child.type == "class_definition"
                else ("function" if child.type == "function_definition" else None)
            )
            if inner_kind == "class":
                fn.children.append(_build_class(child, source, path, package))
            elif inner_kind == "function":
                fn.children.append(_build_function(child, source, path, package, kind="function"))
    return fn


def _build_class(node: tree_sitter.Node, source: bytes, path: Path, package: str | None) -> SourceNode:
    cls = SourceNode(
        kind="class",
        name=_name_of(node, source),
        span=_span(node),
        path=path,
        language="python",
        package=package,
    )
    body = _body_of(node)
    if body is not None:
        for child in body.children:
            if child.type == "function_definition":
                cls.children.append(_build_function(child, source, path, package, kind="method"))
            elif child.type == "decorated_definition":
                # Look at the inner definition.
                inner = child.child_by_field_name("definition")
                if inner is not None and inner.type == "function_definition":
                    cls.children.append(_build_function(inner, source, path, package, kind="method"))
                elif inner is not None and inner.type == "class_definition":
                    cls.children.append(_build_class(inner, source, path, package))
            elif child.type == "class_definition":
                cls.children.append(_build_class(child, source, path, package))
    return cls


def _imports_at(file_root: tree_sitter.Node, source: bytes) -> list[Reference]:
    refs: list[Reference] = []
    for child in file_root.children:
        if child.type == "import_statement":
            # `import x`, `import x.y`, `import x as y`
            for descendant in child.children:
                if descendant.type == "dotted_name":
                    name = _text(descendant, source)
                    refs.append(
                        Reference(
                            kind="import",
                            target_name=name.split(".")[0],
                            target_module=name,
                            site=_span(child),
                            attrs={},
                        )
                    )
                elif descendant.type == "aliased_import":
                    n = descendant.child_by_field_name("name")
                    a = descendant.child_by_field_name("alias")
                    module_name = _text(n, source) if n is not None else None
                    alias = _text(a, source) if a is not None else module_name
                    refs.append(
                        Reference(
                            kind="import",
                            target_name=alias or "",
                            target_module=module_name,
                            site=_span(child),
                            attrs={"as": alias},
                        )
                    )
        elif child.type == "import_from_statement":
            # `from x import y`, `from x import y as z`
            module_node = child.child_by_field_name("module_name")
            module = _text(module_node, source) if module_node is not None else None
            module_start = module_node.start_byte if module_node is not None else None
            for n in child.named_children:
                if module_start is not None and n.start_byte == module_start:
                    continue
                if n.type == "dotted_name" or n.type == "identifier":
                    refs.append(
                        Reference(
                            kind="import",
                            target_name=_text(n, source),
                            target_module=module,
                            site=_span(child),
                            attrs={},
                        )
                    )
                elif n.type == "aliased_import":
                    name_node = n.child_by_field_name("name")
                    alias_node = n.child_by_field_name("alias")
                    name = _text(name_node, source) if name_node is not None else ""
                    alias = _text(alias_node, source) if alias_node is not None else name
                    refs.append(
                        Reference(
                            kind="import",
                            target_name=alias,
                            target_module=module,
                            site=_span(child),
                            attrs={"as": alias, "original": name},
                        )
                    )
    return refs


def _has_main_block(file_root: tree_sitter.Node, source: bytes) -> bool:
    """Detect `if __name__ == "__main__":` at file scope."""
    for child in file_root.children:
        if child.type == "if_statement":
            cond = child.child_by_field_name("condition")
            if cond is not None:
                cond_text = _text(cond, source)
                if "__name__" in cond_text and "__main__" in cond_text:
                    return True
    return False


def _has_importable_symbols(file_root: tree_sitter.Node, source: bytes) -> bool:
    """Detect public top-level def/class/assignment.

    A symbol is "importable" if it sits at module scope and its name does not
    start with an underscore.
    """
    for child in file_root.children:
        if child.type in {"function_definition", "class_definition"}:
            name = _name_of(child, source)
            if name and not name.startswith("_"):
                return True
        elif child.type == "decorated_definition":
            inner = child.child_by_field_name("definition")
            if inner is not None:
                name = _name_of(inner, source)
                if name and not name.startswith("_"):
                    return True
        elif child.type == "assignment":
            left = child.child_by_field_name("left")
            if left is not None:
                txt = _text(left, source)
                if txt and not txt.startswith("_") and txt.isidentifier():
                    return True
    return False


def _all_exports_at(file_root: tree_sitter.Node, source: bytes) -> list[Reference]:
    """Capture `__all__ = ['x', 'y']` exports at the top level.

    In tree-sitter-python the assignment node is a direct child of the module
    root (NOT wrapped in expression_statement as in some other grammars).
    """
    refs: list[Reference] = []
    for child in file_root.children:
        # tree-sitter-python: assignment is a direct child of module
        if child.type == "assignment":
            ass = child
        elif child.type == "expression_statement":
            # fallback in case grammar changes
            ass = next((c for c in child.children if c.type == "assignment"), None)
            if ass is None:
                continue
        else:
            continue
        left = ass.child_by_field_name("left")
        right = ass.child_by_field_name("right")
        if left is None or right is None:
            continue
        if _text(left, source) != "__all__":
            continue
        # right is a list/tuple of strings
        for item in right.named_children:
            if item.type == "string":
                name = _text(item, source).strip("'\"")
                refs.append(
                    Reference(
                        kind="export",
                        target_name=name,
                        target_module=None,
                        site=_span(item),
                        attrs={"via_all": True},
                    )
                )
    return refs


class PythonParser(LanguageParser):
    name = "python"
    file_extensions = (".py",)

    @property
    def grammar(self) -> tree_sitter.Language:
        return get_language("python")

    def parse(self, path: Path, source: bytes, *, package: str | None = None) -> SourceNode:
        parser = tree_sitter.Parser(self.grammar)
        tree = parser.parse(source)
        root = tree.root_node

        file_node = SourceNode(
            kind="file",
            name=None,
            span=_span(root),
            path=path,
            language="python",
            package=package,
        )
        errors = _collect_parse_errors(root)
        if errors:
            file_node.attrs["parse_errors"] = errors
        file_node.attrs["_has_main_block"] = _has_main_block(root, source)
        file_node.attrs["_has_importable_symbols"] = _has_importable_symbols(root, source)

        for child in root.children:
            if child.type == "function_definition":
                file_node.children.append(_build_function(child, source, path, package, kind="function"))
            elif child.type == "class_definition":
                file_node.children.append(_build_class(child, source, path, package))
            elif child.type == "decorated_definition":
                inner = child.child_by_field_name("definition")
                if inner is not None and inner.type == "function_definition":
                    file_node.children.append(_build_function(inner, source, path, package, kind="function"))
                elif inner is not None and inner.type == "class_definition":
                    file_node.children.append(_build_class(inner, source, path, package))

        file_node.refs.extend(_imports_at(root, source))
        file_node.refs.extend(_all_exports_at(root, source))
        return file_node
