"""Project a SourceTree into graph records aligned to code-graph-io's SQLite schema."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from code_parser.tree import SourceNode

NodeKey = tuple[str, str, str | None]  # (kind, name, path)

# Containers whose qualified name prefixes their children's names. "function" is
# deliberate (not just class->method): symbols nested inside a function/method are
# qualified by the enclosing name (e.g. outer.inner), which the enclosing-class
# tracking builds on.
_CONTAINER_KINDS = frozenset({"class", "function", "method"})

# Call receivers that bind to the enclosing class (intra-class calls).
_SELF_RECEIVERS = frozenset({"self", "this"})


@dataclass(frozen=True)
class GraphNode:
    kind: str
    name: str
    path: str
    line: int | None
    attrs: dict[str, Any] = field(default_factory=dict)
    # Transport-only: raw source byte span, consumed by code-graph-io's token
    # counter at build time. NOT persisted to attrs_json. Defaults of None
    # keep every other GraphNode construction path valid.
    start_byte: int | None = None
    end_byte: int | None = None


@dataclass(frozen=True)
class GraphEdge:
    src: NodeKey
    dst: NodeKey
    kind: str
    attrs: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GraphRecords:
    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]


def _qualify(prefix: str, name: str) -> str:
    return f"{prefix}.{name}" if prefix else name


def _key(node: SourceNode, qname: str) -> NodeKey:
    return (node.kind, qname, str(node.path))


def _emit_node(node: SourceNode, qname: str) -> GraphNode:
    return GraphNode(
        kind=node.kind,
        name=qname,
        path=str(node.path),
        line=None if node.kind == "file" else node.span.start_line,
        attrs=dict(node.attrs),
        start_byte=node.span.start_byte,
        end_byte=node.span.end_byte,
    )


def _walk(
    node: SourceNode,
    nodes: list[GraphNode],
    edges: list[GraphEdge],
    *,
    prefix: str = "",
    enclosing_class: str | None = None,
) -> None:
    name = node.name if node.name is not None else str(node.path)
    qname = _qualify(prefix, name)
    nodes.append(_emit_node(node, qname))
    parent_key = _key(node, qname)

    # Children of a container are qualified by the container's own name; a file
    # resets the prefix to "" so top-level symbols stay bare.
    child_prefix = qname if node.kind in _CONTAINER_KINDS else ""
    # Propagate the enclosing class: set to qname at a class boundary, reset to
    # None at file scope, and pass through unchanged for functions/methods so
    # nested functions inside a method still see the class.
    if node.kind == "class":
        child_enclosing = qname
    elif node.kind == "file":
        child_enclosing = None
    else:
        child_enclosing = enclosing_class
    for child in node.children:
        child_name = child.name if child.name is not None else str(child.path)
        child_qname = _qualify(child_prefix, child_name)
        edges.append(
            GraphEdge(
                src=parent_key,
                dst=_key(child, child_qname),
                kind="contains",
                attrs={},
            )
        )
        _walk(child, nodes, edges, prefix=child_prefix, enclosing_class=child_enclosing)

    # `enclosing_class` (the argument) is the class enclosing THIS node and drives
    # self/this qualification below; `child_enclosing` (above) is what propagates to children.
    for ref in node.refs:
        if ref.kind == "call":
            target = ref.target_name
            # Only self/this receivers get a class-qualified target; other receivers stay bare.
            if ref.attrs.get("receiver") in _SELF_RECEIVERS and enclosing_class is not None:
                target = _qualify(enclosing_class, ref.target_name)
            edges.append(
                GraphEdge(
                    src=parent_key,
                    # Upsert stores this pathless code-symbol target as
                    # kind='unresolved_symbol' while preserving symbol_kind on
                    # the edge for resolve.sweep().
                    dst=("function", target, None),
                    kind="calls",
                    attrs=dict(ref.attrs),
                )
            )
        elif ref.kind == "import":
            edges.append(
                GraphEdge(
                    src=parent_key,
                    dst=("file", ref.target_name, ref.target_module),
                    kind="imports",
                    attrs=dict(ref.attrs),
                )
            )
        elif ref.kind == "export":
            edges.append(
                GraphEdge(
                    src=parent_key,
                    dst=(ref.attrs.get("symbol_kind", "function"), ref.target_name, None),
                    kind="exports",
                    attrs=dict(ref.attrs),
                )
            )


def to_graph_records(tree: SourceNode) -> GraphRecords:
    """Project a parsed SourceTree onto graph records."""
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []
    _walk(tree, nodes, edges)
    return GraphRecords(nodes=tuple(nodes), edges=tuple(edges))
