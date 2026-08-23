"""Render lists of dataclass records as JSON or aligned-column human output.

Public formatter module for code_graph_io. Shared by agent-workspace-cli's gw graph
modules and other agent-workspace surfaces without pulling CLI code back into code-graph-io.

Per-kind formatters (format_package, format_app, format_path, format_repo,
format_entry_point, format_suite, format_dependency,
format_builtin, format_agent_plugin, format_symbol, format_matches) extracted
from the corresponding q_describe_*.py inline printers to form a single source
of truth.
"""

from __future__ import annotations

import dataclasses
import json as _json
from collections.abc import Callable, Iterable, Mapping
from typing import TYPE_CHECKING, Any, NamedTuple

if TYPE_CHECKING:
    # Type-only: render must not import queries at module load (see the cycle
    # note in `_is_importer_batch`). Annotations are strings under
    # `from __future__ import annotations`, so this never runs.
    from code_graph_io.queries import (
        AgentPluginDescription,
        AppDescription,
        BuiltinDescription,
        ChildNode,
        DependencyDescription,
        EntryPointDescription,
        ExportRecord,
        NodeRecord,
        PackageDescription,
        PathDescription,
        RepoDescription,
        SuiteDescription,
        SymbolDescription,
    )


def _to_dict(record: object) -> dict[str, Any]:
    if dataclasses.is_dataclass(record) and not isinstance(record, type):
        out: dict[str, Any] = dataclasses.asdict(record)
        return out
    if isinstance(record, Mapping):
        return {str(key): value for key, value in record.items()}
    raise TypeError(f"record is not renderable as a mapping: {type(record).__name__}")


def _is_importer_batch(rows: list[Any]) -> bool:
    # Avoid an explicit import cycle (render must not import queries at module load).
    return bool(rows) and type(rows[0]).__name__ == "ImporterRecord"


def _importer_human(rows: list[Any]) -> str:
    if not rows:
        return ""
    formatted = [
        {
            "path": r.path,
            "symbols": "(" + ", ".join(r.symbols) + ")" if r.symbols else "",
            "depth": str(r.depth),
        }
        for r in rows
    ]
    keys = ["path", "symbols", "depth"]
    widths = {k: max(len(row[k]) for row in formatted) for k in keys}
    return "\n".join("  ".join(row[k].ljust(widths[k]) for k in keys) for row in formatted)


def _importer_json(rows: list[Any]) -> str:
    flat: list[dict[str, Any]] = []
    for r in rows:
        if r.symbols:
            for sym in r.symbols:
                flat.append({"path": r.path, "symbol": sym, "depth": r.depth})
        else:
            flat.append({"path": r.path, "symbol": None, "depth": r.depth})
    return _json.dumps(flat, default=str)


class Attr(NamedTuple):
    """One attributes-block entry. `human` is the pre-rendered human value;
    `json` is the raw value placed under `key` in JSON output."""

    label: str
    key: str
    human: str
    json: object

    @classmethod
    def scalar(cls, label: str, key: str, value: object) -> Attr:
        human = "(none)" if value is None or value == "" else str(value)
        return cls(label, key, human, value)

    @classmethod
    def joined(cls, label: str, key: str, values: list[str]) -> Attr:
        return cls(label, key, ", ".join(values) or "(none)", list(values))


class Rel(NamedTuple):
    """One relationships-block entry. Human renders as a comma-joined list;
    JSON places `values` under `key`."""

    label: str
    key: str
    values: list[str]


def _pluralize(word: str, n: int) -> str:
    if n == 1:
        return word
    if word.endswith(("s", "x", "z", "ch", "sh")):
        return word + "es"
    if word.endswith("y") and word[-2:-1] not in "aeiou":
        return word[:-1] + "ies"
    return word + "s"


def _counts_human(counts: dict[str, int]) -> str:
    if not counts:
        return "(none)"
    return " · ".join(f"{n} {_pluralize(k, n)}" for k, n in counts.items())


def _aligned_block(title: str, pairs: list[tuple[str, str]]) -> list[str]:
    """Render a titled block: title line then `  label: value` lines aligned to
    the widest label. Returns [] when pairs is empty (caller omits the section)."""
    if not pairs:
        return []
    width = max(len(label) for label, _ in pairs)
    lines = [title]
    for label, value in pairs:
        lines.append(f"  {(label + ':').ljust(width + 1)} {value}")
    return lines


# Source-code symbol kinds carry no uri; their node name is the readable label.
_SYMBOL_KINDS = ("function", "class", "method", "type")


def _child_label(child: ChildNode) -> str:
    """Display identity for a children-tree node.

    Source-code symbols (function/class/method/type) have no uri, so show their
    node name. Everything else: uri -> path:line -> path.
    """
    name = child.name
    if child.kind in _SYMBOL_KINDS and name:
        return name
    if child.uri:
        return child.uri
    if child.path is not None and child.line is not None:
        return f"{child.path}:{child.line}"
    if child.path is not None:
        return child.path
    return "(unknown)"


def _ascii_tree(children: list[ChildNode], prefix: str = "") -> list[str]:
    """Depth-first box-drawing render of a ChildNode tree. 3-char connectors."""
    lines: list[str] = []
    for i, child in enumerate(children):
        last = i == len(children) - 1
        connector = "└─ " if last else "├─ "
        lines.append(f"{prefix}{connector}{_child_label(child)}")
        if child.children:
            extension = "   " if last else "│  "
            lines.extend(_ascii_tree(child.children, prefix + extension))
    return lines


def _children_json(children: list[ChildNode]) -> list[dict[str, Any]]:
    """Nested {kind,uri,path,line,name,children} array mirroring the tree."""
    return [dataclasses.asdict(c) for c in children]


def describe_block(
    *,
    kind: str,
    name: str,
    identity_label: str,
    identity_value: str,
    attributes: list[Attr],
    relationships: list[Rel],
    nav: list[str],
    fmt: str,
    children: list[ChildNode] | None = None,
    children_depth: int | None = None,
) -> str:
    """Single sectioned-spine builder shared by every format_<kind>.

    Human: `<kind> <name>` header, indented identity line, then the present
    `attributes`/`relationships`/nav sections (empty sections omitted), each
    separated by a blank line, labels aligned within their block.
    JSON: {kind, name, <identity_label>, attributes, relationships, nav} —
    empty sections kept as {}/[] for a stable machine shape.

    `children` (a ChildNode tree) renders a depth-bounded containment section
    between `relationships` and `nav`; when present a `gw graph describe <sel>
    --depth N+1` hint is appended to `nav` so the reader can expand one level
    deeper (<sel> is the path-style identity for file/symbol kinds, else the
    resolvable name). An empty/omitted tree leaves the spine unchanged.
    """
    has_children = bool(children)
    nav = list(nav)
    if has_children:
        deeper_sel = identity_value if identity_label == "path" else name
        nav.append(f"gw graph describe {deeper_sel} --depth {(children_depth or 0) + 1}")

    if fmt == "json":
        payload: dict[str, object] = {
            "kind": kind,
            "name": name,
            identity_label: identity_value,
            "attributes": {a.key: a.json for a in attributes},
            "relationships": {r.key: r.values for r in relationships},
        }
        if has_children:
            payload["children_depth"] = children_depth
            payload["children"] = _children_json(children)  # type: ignore[arg-type]
        payload["nav"] = list(nav)
        return _json.dumps(payload, default=str)
    if fmt != "human":
        raise ValueError(f"unknown format: {fmt!r}")

    lines = [f"{kind} {name}", f"  {identity_label}: {identity_value}"]
    attr_lines = _aligned_block("attributes", [(a.label, a.human) for a in attributes])
    if attr_lines:
        lines.append("")
        lines.extend(attr_lines)
    rel_lines = _aligned_block("relationships", [(r.label, ", ".join(r.values)) for r in relationships])
    if rel_lines:
        lines.append("")
        lines.extend(rel_lines)
    if has_children:
        lines.append("")
        lines.append(f"children (depth {children_depth})")
        lines.extend(_ascii_tree(children))  # type: ignore[arg-type]
    if nav:
        lines.append("")
        lines.extend(f"→ {cmd}" for cmd in nav)
    return "\n".join(lines)


def render(
    records: Iterable[Any],
    fmt: str,
    *,
    cap: int | None = None,
    on_truncate: Callable[[int, int], None] | None = None,
) -> str:
    """Render `records` as `fmt` ('human' or 'json'), optionally capping rows.

    When `cap` is set and `len(rows) > cap`, only the first `cap` rows are
    rendered. For `fmt='human'`, a trailing line `... showing {cap} of {total}
    (truncated)` is appended. For `fmt='json'`, the truncation is silent (no
    envelope wrap — flat array of the first `cap` rows). When truncation
    fires and `on_truncate` is provided, it is invoked with `(cap, total)` so
    the caller can emit a side-channel notice (e.g. stderr). `render` itself
    never writes outside its return value.

    `cap=None` (the default) preserves the pass-through behavior
    for every caller that does not opt in.
    """
    rows = list(records)
    total = len(rows)
    truncated = cap is not None and total > cap
    if truncated:
        row_cap = cap
        if row_cap is None:
            raise RuntimeError("truncated render requires a row cap")
        rows = rows[:row_cap]
        if on_truncate is not None:
            on_truncate(row_cap, total)

    if _is_importer_batch(rows):
        if fmt == "json":
            return _importer_json(rows)
        if fmt == "human":
            out = _importer_human(rows)
            if truncated:
                trailer = f"... showing {cap} of {total} (truncated)"
                return f"{out}\n{trailer}" if out else trailer
            return out
        raise ValueError(f"unknown format: {fmt!r}")

    dicts = [_to_dict(r) for r in rows]
    if fmt == "json":
        return _json.dumps(dicts, default=str)
    if fmt == "human":
        if not dicts:
            return ""
        keys = list(dicts[0].keys())
        widths = {k: max(len(str(r.get(k, ""))) for r in [*dicts, dict.fromkeys(keys, k)]) for k in keys}
        lines = []
        for r in dicts:
            lines.append("  ".join(str(r.get(k, "")).ljust(widths[k]) for k in keys))
        if truncated:
            if cap is None:
                raise RuntimeError("truncated render requires a row cap")
            lines.append(f"... showing {cap} of {total} (truncated)")
        return "\n".join(lines)
    raise ValueError(f"unknown format: {fmt!r}")


# ── Per-kind formatters (extracted from q_describe_*.py inline printers) ──────


def _package_relationships(desc: PackageDescription) -> list[Rel]:
    rels: list[Rel] = []
    if desc.internal_dependencies:
        rels.append(Rel("internal deps", "internal_dependencies", list(desc.internal_dependencies)))
    if desc.internal_dependents:
        rels.append(Rel("internal dependents", "internal_dependents", list(desc.internal_dependents)))
    if desc.entry_points:
        rels.append(Rel("entry_points", "entry_points", [ep.name for ep in desc.entry_points]))
    if desc.test_suites:
        rels.append(Rel("test_suites", "test_suites", [s.name for s in desc.test_suites]))
    return rels


def _package_nav(name: str) -> list[str]:
    return [f"gw graph what-tests {name}", f"gw graph list-entry-points {name}"]


def format_package(
    desc: PackageDescription,
    fmt: str,
    *,
    children: list[ChildNode] | None = None,
    effective_depth: int | None = None,
) -> str:
    """Format a PackageDescription on the sectioned spine."""
    attributes = [
        Attr.scalar("language", "language", desc.language),
        Attr.scalar("version", "version", desc.version),
        Attr.scalar("files", "files", len(desc.files)),
        Attr("counts", "counts", _counts_human(desc.counts), desc.counts),
    ]
    return describe_block(
        kind="package",
        name=desc.name,
        identity_label="uri",
        identity_value=f"pkg:{desc.name}",
        attributes=attributes,
        relationships=_package_relationships(desc),
        nav=_package_nav(desc.name),
        fmt=fmt,
        children=children,
        children_depth=effective_depth,
    )


def format_app(
    desc: AppDescription, fmt: str, *, children: list[ChildNode] | None = None, effective_depth: int | None = None
) -> str:
    """Format an AppDescription on the sectioned spine (including app_kind and signals)."""
    rels: list[Rel] = []
    if desc.entry_points:
        rels.append(Rel("entry_points", "entry_points", [ep.name for ep in desc.entry_points]))
    if desc.test_suites:
        rels.append(Rel("test_suites", "test_suites", [s.name for s in desc.test_suites]))
    attributes = [
        Attr.scalar("language", "language", desc.language),
        Attr.scalar("version", "version", desc.version),
        Attr.scalar("app_kind", "app_kind", desc.app_kind),
        Attr.joined("signals", "signals", list(desc.app_signals)),
        Attr.scalar("files", "files", len(desc.files)),
        Attr("counts", "counts", _counts_human(desc.counts), desc.counts),
    ]
    return describe_block(
        kind="app",
        name=desc.name,
        identity_label="uri",
        identity_value=f"app:{desc.name}",
        attributes=attributes,
        relationships=rels,
        nav=_package_nav(desc.name),
        fmt=fmt,
        children=children,
        children_depth=effective_depth,
    )


def _node_label(rec: NodeRecord | ExportRecord) -> str:
    tc = (getattr(rec, "attrs", None) or {}).get("token_count")
    tok = f" ({tc} tokens)" if tc is not None else ""  # NodeRecord children carry it; ExportRecord has no attrs
    if getattr(rec, "line", None) is not None:
        return f"{rec.kind} {rec.name} (line {rec.line}){tok}"
    return f"{rec.kind} {rec.name}{tok}"


def _role_flags_human(role_flags: dict[str, bool] | None) -> str:
    if not role_flags:
        return "(none)"
    on = [k for k, v in role_flags.items() if v]
    return ", ".join(on) or "(none)"


def format_path(
    desc: PathDescription, fmt: str, *, children: list[ChildNode] | None = None, effective_depth: int | None = None
) -> str:
    """Format a PathDescription (a file node) on the spine."""
    attributes = [Attr("role_flags", "role_flags", _role_flags_human(desc.role_flags), desc.role_flags)]
    if desc.token_count is not None:  # deriver v7 file token_count (Design decision 8)
        attributes.append(Attr.scalar("tokens", "token_count", desc.token_count))
    rels: list[Rel] = []
    if desc.children:
        rels.append(Rel("children", "children", [_node_label(c) for c in desc.children]))
    if desc.imports:
        rels.append(Rel("imports", "imports", [i.name for i in desc.imports]))
    if desc.exports:
        rels.append(Rel("exports", "exports", [_node_label(e) for e in desc.exports]))
    return describe_block(
        kind="file",
        name=desc.path,
        identity_label="path",
        identity_value=desc.path,
        attributes=attributes,
        relationships=rels,
        nav=[f"gw graph imported-by {desc.path}"],
        fmt=fmt,
        children=children,
        children_depth=effective_depth,
    )


def format_repo(
    desc: RepoDescription, fmt: str, *, children: list[ChildNode] | None = None, effective_depth: int | None = None
) -> str:
    """Format a RepoDescription on the sectioned spine."""
    attributes = [
        Attr.scalar("url", "url", desc.url),
        Attr.scalar("default_branch", "default_branch", desc.default_branch),
        Attr.scalar("owner", "owner", desc.owner),
        Attr.scalar("package_count", "package_count", desc.package_count),
    ]
    return describe_block(
        kind="repository",
        name=desc.name,
        identity_label="uri",
        identity_value=desc.uri,
        attributes=attributes,
        relationships=[],
        nav=["gw graph list --kind package", "gw graph list --kind app"],
        fmt=fmt,
        children=children,
        children_depth=effective_depth,
    )


def format_entry_point(desc: EntryPointDescription, fmt: str) -> str:
    """Format an EntryPointDescription on the sectioned spine."""
    attributes = [
        Attr.scalar("kind", "kind", desc.kind),
        Attr.scalar("callable", "callable", desc.callable),
        Attr.scalar("path", "path", desc.implemented_by_path),
        Attr.scalar("source", "source", desc.source),
    ]
    nav = [f"gw graph describe {desc.implemented_by_path}"] if desc.implemented_by_path else []
    return describe_block(
        kind="entry_point",
        name=desc.name,
        identity_label="uri",
        identity_value=desc.uri,
        attributes=attributes,
        relationships=[],
        nav=nav,
        fmt=fmt,
    )


def format_suite(
    desc: SuiteDescription, fmt: str, *, children: list[ChildNode] | None = None, effective_depth: int | None = None
) -> str:
    """Format a SuiteDescription on the sectioned spine."""
    attributes = [
        Attr.scalar("kind", "kind", desc.kind),
        Attr.scalar("files", "files", desc.file_count),
    ]
    return describe_block(
        kind="test_suite",
        name=desc.name,
        identity_label="uri",
        identity_value=desc.uri,
        attributes=attributes,
        relationships=[],
        nav=[],
        fmt=fmt,
        children=children,
        children_depth=effective_depth,
    )


def format_dependency(desc: DependencyDescription, fmt: str) -> str:
    """Format a DependencyDescription on the sectioned spine."""
    attributes = [
        Attr.scalar("ecosystem", "ecosystem", desc.ecosystem),
        Attr.joined("versions_in_use", "versions_in_use", list(desc.versions_in_use)),
        Attr.scalar("ambiguous", "ambiguous", desc.ambiguous),
    ]
    rels = [Rel("used_by", "used_by", list(desc.used_by))] if desc.used_by else []
    rels.append(Rel("implemented_by", "implemented_by", list(desc.implemented_by)))
    return describe_block(
        kind="dependency",
        name=desc.name,
        identity_label="uri",
        identity_value=desc.uri,
        attributes=attributes,
        relationships=rels,
        nav=[],
        fmt=fmt,
    )


def format_builtin(desc: BuiltinDescription, fmt: str) -> str:
    """Format a BuiltinDescription on the sectioned spine."""
    attributes = [
        Attr.scalar("language", "language", desc.language),
        Attr.scalar("module_name", "module_name", desc.module_name),
    ]
    rels = [Rel("used_by", "used_by", list(desc.used_by))] if desc.used_by else []
    return describe_block(
        kind="builtin",
        name=desc.module_name,
        identity_label="uri",
        identity_value=desc.uri,
        attributes=attributes,
        relationships=rels,
        nav=[],
        fmt=fmt,
    )


def format_agent_plugin(desc: AgentPluginDescription, fmt: str) -> str:
    """Format an AgentPluginDescription on the sectioned spine."""
    attributes = [
        Attr.scalar("ecosystem", "ecosystem", desc.ecosystem),
        Attr.scalar("version", "version", desc.version),
        Attr.scalar("commands", "commands", len(desc.commands)),
        Attr.scalar("agents", "agents", len(desc.agents)),
        Attr.scalar("skills", "skills", len(desc.skills)),
        Attr.scalar("scripts", "scripts", len(desc.scripts)),
        Attr.scalar("hooks", "hooks", len(desc.hooks)),
        Attr.scalar("mcp_servers", "mcp_servers", len(desc.mcp_servers)),
    ]
    return describe_block(
        kind="agent_plugin",
        name=desc.name,
        identity_label="uri",
        identity_value=desc.uri,
        attributes=attributes,
        relationships=[],
        nav=["gw graph list --kind agent_plugin"],
        fmt=fmt,
    )


def format_symbol(
    desc: SymbolDescription, fmt: str, *, children: list[ChildNode] | None = None, effective_depth: int | None = None
) -> str:
    """Format a SymbolDescription (function/class/method/type) on the spine."""
    loc = f"{desc.path}:{desc.line}" if desc.line is not None else (desc.path or "(unknown)")
    exported = f"yes (from {desc.exported_from})" if desc.exported_from else "no"
    attributes = [Attr("exported", "exported", exported, bool(desc.exported_from))]
    if desc.token_count is not None:  # deriver v7 symbol token_count (Design decision 8)
        attributes.append(Attr.scalar("tokens", "token_count", desc.token_count))
    if desc.package:
        attributes.append(Attr.scalar("package", "package", desc.package))
    rels: list[Rel] = []
    if desc.callers:
        rels.append(Rel("callers", "callers", [c.name for c in desc.callers]))
    if desc.callees:
        rels.append(Rel("callees", "callees", [c.name for c in desc.callees]))
    return describe_block(
        kind=desc.kind,
        name=desc.name,
        identity_label="path",
        identity_value=loc,
        attributes=attributes,
        relationships=rels,
        nav=[f"gw graph callers {desc.name} --depth 3", f"gw graph callees {desc.name} --depth 3"],
        fmt=fmt,
        children=children,
        children_depth=effective_depth,
    )


def format_matches(records: Iterable[Any], fmt: str) -> str:
    """Format MatchRecord disambiguation entries as human text or JSON."""
    rows = list(records)
    if fmt == "json":
        return _json.dumps([dataclasses.asdict(r) for r in rows], default=str)
    if fmt == "human":
        lines = []
        for r in rows:
            mid = f"  {r.address}" if r.address else ""
            lines.append(f"{r.kind}{mid}  → {r.command}")
        return "\n".join(lines)
    raise ValueError(f"unknown format: {fmt!r}")
