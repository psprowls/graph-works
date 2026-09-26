"""Read-only queries over the code graph. All callers open a read-only conn."""

from __future__ import annotations

import dataclasses
import json
import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from code_graph_io.uri import dependency_identifier_from_path

# A SQL bind-parameter list, built up alongside a WHERE clause. sqlite3 accepts
# str / int / float / bytes / None, and these lists mix them freely.
SqlParams = list[Any]

# DB node kind -> the value accepted by `gw graph describe --kind`.
# Entity kinds whose CLI value differs from the DB kind, plus the code kinds
# (identity) and file -> path. Kinds absent here have no describer and fall
# through to a bare-name menu command.
_CLI_KIND = {
    "package": "package",
    "app": "app",
    "dependency": "dependency",
    "test_suite": "test_suite",
    "agent_plugin": "agent_plugin",
    "entry_point": "entry_point",
    "function": "function",
    "class": "class",
    "method": "method",
    "type": "type",
    "file": "path",
}

# DB node kinds that carry a path:line and dispatch to the symbol describer.
_CODE_KINDS = frozenset({"function", "class", "method", "type"})

# Files contained by a package OR app, matched case-insensitively on the short
# `name` column. Shared by find() and describe_symbol() so their --in-package
# semantics (notably: app names narrow too, not just package names) cannot drift.
# NOTE: a package and an app sharing a name would union both — acceptable; names
# are effectively unique in practice.
_FILES_IN_PACKAGE_SUBQUERY = (
    "(SELECT f.path FROM nodes p "
    "JOIN edges ce ON ce.src = p.id AND ce.kind='contains' "
    "JOIN nodes f ON ce.dst = f.id AND f.kind='file' "
    "WHERE p.kind IN ('package', 'app') AND LOWER(p.name) = LOWER(?))"
)

_VALID_KINDS = frozenset(
    {
        "function",
        "class",
        "method",
        "file",
        "package",
        "repository",
        "subpackage",
        "entry_point",
        "test_suite",
        # admitted entity kinds for the wiki entity writer.
        "dependency",
        # agent-plugin entity: a claude-code plugin under development in this repo.
        "agent_plugin",
        # stdlib module imports (Python via sys.stdlib_module_names;
        # Node via require('module').builtinModules)
        "builtin",
        # app-classified packages (scanner-derived kind)
        "app",
        # TypeScript interface/type-alias/enum nodes
        "type",
        # Explicit unresolved call/export target placeholders.
        "unresolved_symbol",
    }
)

# App framework kinds derived by classification.classify().
# Write-time gate — keep in sync with _FRAMEWORK_PRECEDENCE in
# code_graph_io/classification.py.
_VALID_APP_KINDS = frozenset({"cli", "electron", "expo", "nextjs", "server", "spa"})

_RESOLVED_FILTER = "(e.attrs_json IS NULL OR json_extract(e.attrs_json, '$.resolution') != 'unresolved')"

# Prunes a candidate symbol whose file is a test file. `is_test` lives only on
# `file` nodes (code_graph_io.import_scan via structural_nodes._is_test_path); a
# definition node carries a `path`, so we join that path back to its file node.
# `{alias}` is the candidate node alias (`src` for callers, `dst` for callees).
_NON_TEST_FILTER = (
    "NOT EXISTS (SELECT 1 FROM nodes f WHERE f.kind='file' "
    "AND f.path = {alias}.path "
    "AND json_extract(f.attrs_json, '$.is_test') = 1)"
)


@dataclass(frozen=True)
class NodeRecord:
    kind: str
    name: str
    path: str | None
    line: int | None
    attrs: dict[str, Any]


@dataclass(frozen=True)
class MatchRecord:
    kind: str  # DB node kind, shown as the menu label
    address: str  # "<path>:<line>" or "" when the node has no path
    command: str  # full "gw graph describe ..." copy-paste suggestion


@dataclass(frozen=True)
class CallRecord:
    name: str
    path: str | None
    line: int | None
    depth: int


@dataclass(frozen=True)
class ChildNode:
    """One node in a describe `children` containment tree.

    Identity is stored raw (uri / path / line / name); the renderer derives the
    display label (source-code symbols have no uri, so they show `name`;
    everything else: uri -> path:line -> path). `children` is the recursively
    expanded subtree (empty at the depth frontier or for leaf kinds).
    """

    kind: str
    uri: str | None
    path: str | None
    line: int | None
    name: str | None = None
    children: list[ChildNode] = field(default_factory=list)


@dataclass(frozen=True)
class SymbolDescription:
    """Fixed-depth-1 dossier for a code symbol (function/class/method/type)."""

    kind: str
    name: str
    path: str | None
    line: int | None
    package: str | None
    exported_from: str | None
    token_count: int | None = None
    callers: list[CallRecord] = field(default_factory=list)
    callees: list[CallRecord] = field(default_factory=list)


@dataclass(frozen=True)
class ImportRecord:
    name: str
    path: str | None


@dataclass(frozen=True)
class RepoDescription:
    name: str
    uri: str
    owner: str | None
    url: str | None
    default_branch: str | None
    package_count: int


@dataclass(frozen=True)
class EntryPointDescription:
    name: str
    uri: str
    kind: str
    callable: str | None
    implemented_by_path: str | None
    source: str


@dataclass(frozen=True)
class SuiteDescription:
    name: str
    uri: str
    kind: str
    file_count: int
    files: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PackageDescription:
    name: str
    language: str
    version: str
    files: list[str]
    counts: dict[str, int]
    entry_points: list[EntryPointDescription] = field(default_factory=list)
    test_suites: list[SuiteDescription] = field(default_factory=list)
    # both directions of the depends_on_package edge.
    internal_dependencies: list[str] = field(default_factory=list)  # outgoing
    internal_dependents: list[str] = field(default_factory=list)  # incoming
    # facts migrated from the implemented Dependency node (ADR 2026-09-07-dependencies); empty
    # when this package does not implement a distributable manifest's
    # Dependency node.
    used_by: list[str] = field(default_factory=list)
    versions_in_use: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AppDescription:
    """Description of an `app` node.

    Mirrors `PackageDescription` field-for-field with two additions:
    `app_kind` (one of `_VALID_APP_KINDS`) and `app_signals` (the sorted
    list of signals that triggered classification).
    """

    name: str
    language: str
    version: str
    app_kind: str
    app_signals: list[str]
    files: list[str]
    counts: dict[str, int]
    entry_points: list[EntryPointDescription] = field(default_factory=list)
    test_suites: list[SuiteDescription] = field(default_factory=list)


@dataclass(frozen=True)
class PathDescription:
    path: str
    children: list[NodeRecord]
    imports: list[NodeRecord]
    role_flags: dict[str, bool] | None = None
    token_count: int | None = None
    exports: list[ExportRecord] = field(default_factory=list)


@dataclass(frozen=True)
class FileDescription:
    """Repository-scoped dossier for the exact File identified by ``uri``."""

    uri: str
    path: str
    children: list[NodeRecord]
    imports: list[ImportRecord]
    imported_by: list[ImporterRecord]
    package: tuple[str, str] | None = None
    role_flags: dict[str, bool] | None = None
    token_count: int | None = None
    exports: list[ExportRecord] = field(default_factory=list)


@dataclass(frozen=True)
class DependencyDescription:
    """Description of a `dependency` node.

    `repository` is the owning `repo:<org>/<repo>` URI (D-004).
    """

    ecosystem: str
    name: str
    uri: str
    repository: str = ""
    versions_in_use: list[str] = field(default_factory=list)
    used_by: list[str] = field(default_factory=list)
    implemented_by: list[str] = field(default_factory=list)

    @property
    def ambiguous(self) -> bool:
        """Whether more than one workspace Package implements this dependency."""
        return len(self.implemented_by) > 1


@dataclass(frozen=True)
class BuiltinDescription:
    """Description of a `builtin` node."""

    language: str
    module_name: str
    uri: str
    used_by: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AgentPluginDescription:
    """Description of an `agent_plugin` node.

    Carries the plugin manifest fields plus the component inventory parsed at
    graph-build time (commands/agents/skills/scripts/hooks/mcp_servers). Each
    component is a plain dict with a stable `id`; they are NOT graph nodes.

    `package_name` is the sibling Package node's name when this plugin root
    also carries a package manifest (facet model: `Package --facet_of-->
    agent_plugin`, linked by `agent_plugins.link_agent_plugin_facets`) —
    `None` for a plain plugin root with no manifest.
    """

    name: str
    uri: str
    ecosystem: str
    version: str
    description: str
    commands: list[dict[str, Any]] = field(default_factory=list)
    agents: list[dict[str, Any]] = field(default_factory=list)
    skills: list[dict[str, Any]] = field(default_factory=list)
    scripts: list[dict[str, Any]] = field(default_factory=list)
    hooks: list[dict[str, Any]] = field(default_factory=list)
    mcp_servers: list[dict[str, Any]] = field(default_factory=list)
    package_name: str | None = None


def _row_to_node(row: Sequence[Any]) -> NodeRecord:
    """Project a SQL row into a NodeRecord.

    Accepts 5-column shape `(kind, name, path, line, attrs_json)`, 6-column
    shape with `uri` appended, or 7-column shape with `repo` appended. When
    `uri` is present, it is folded back into `attrs` under the `"uri"` key so
    callers can read it uniformly from `node.attrs["uri"]` (the upsert layer
    pops `uri` out of attrs and into a dedicated column at write time —
    projecting it back keeps the read surface symmetric for callers). The
    7-column shape additionally folds the `repo` column into `attrs["repo"]`
    so repository attribution can resolve an entity's repository from the
    authoritative `repo` column even when its URI doesn't carry a repo segment.
    """
    repo = None
    if len(row) == 7:
        kind, name, path, line, attrs_json, uri, repo = row
    elif len(row) == 6:
        kind, name, path, line, attrs_json, uri = row
    else:
        kind, name, path, line, attrs_json = row
        uri = None
    attrs = json.loads(attrs_json) if attrs_json else {}
    if uri:
        attrs["uri"] = uri
    if repo:
        attrs["repo"] = repo
    return NodeRecord(kind=kind, name=name, path=path, line=line, attrs=attrs)


def _load_entry_point_description(row: Sequence[Any]) -> EntryPointDescription:
    """Project a raw EntryPoint SQL row into a Description.

    Expected row shape: (name, uri, attrs_json, impl_path).
    attrs_json is parsed; `kind`, `callable`, `source` are read from it.
    `implemented_by_path` is the joined File.path (may be None).
    """
    name, uri, attrs_json, impl_path = row
    attrs = json.loads(attrs_json) if attrs_json else {}
    # The emitter writes the kind as `entry_kind` in attrs_json; fall back to
    # `kind` for forward compatibility / projector-level unit tests.
    kind = attrs.get("entry_kind") or attrs.get("kind", "")
    return EntryPointDescription(
        name=name,
        uri=uri or "",
        kind=kind,
        callable=attrs.get("callable"),
        implemented_by_path=impl_path,
        source=attrs.get("source", ""),
    )


def _load_suite_description(row: Sequence[Any]) -> SuiteDescription:
    """Project a TestSuite row into a SuiteDescription.

    Expected row shape: (name, uri, attrs_json, file_count).
    """
    name, uri, attrs_json, file_count = row
    attrs = json.loads(attrs_json) if attrs_json else {}
    return SuiteDescription(
        name=name,
        uri=uri or "",
        kind=attrs.get("suite_kind", ""),
        file_count=int(file_count or 0),
    )


def find(
    conn: sqlite3.Connection,
    *,
    name: str | None = None,
    kind: str | None = None,
    in_package: str | None = None,
) -> list[NodeRecord]:
    """Find nodes by name, kind, and/or containing package.

    Filters AND-combine: passing multiple filters narrows results to nodes
    matching all of them. `in_package` matches the short package name
    (case-insensitive, exact) — i.e. the `name` column of the containing
    `package` node — and selects every node whose `path` is contained by
    that package via a `contains`-edge from package → file.

    `conn` must be a `sqlite3.Connection` opened with `mode=ro`.

    Raises:
        ValueError: when `kind` is provided but is not in `_VALID_KINDS`,
            or when all of `name`, `kind`, and `in_package` are None.
    """
    if kind is not None and kind not in _VALID_KINDS:
        raise ValueError(f"unknown kind {kind!r}; valid: {sorted(_VALID_KINDS)}")
    if name is None and kind is None and in_package is None:
        raise ValueError("find requires at least one of name, kind, or in_package")

    where_parts: list[str] = []
    params: SqlParams = []
    if name is not None:
        # Suffix-aware: a bare leaf matches both an exact node and any qualified
        # `*.name` node (e.g. `save` reaches `Foo.save`). LIKE '%.name' can't use
        # the (kind, name) index (leading wildcard); acceptable at single-repo scale.
        where_parts.append("(n.name = ? OR n.name LIKE '%.' || ?)")
        params.extend([name, name])
    if kind is not None:
        where_parts.append("n.kind = ?")
        params.append(kind)
    if in_package is not None:
        where_parts.append(f"n.path IN {_FILES_IN_PACKAGE_SUBQUERY}")
        params.append(in_package)

    sql = "SELECT kind, name, path, line, attrs_json FROM nodes n WHERE " + " AND ".join(where_parts)
    # Preserve historical ORDER BY for kind-only queries — existing callers
    # (e.g. test_find_per_kind) rely on alphabetical ordering.
    if name is None and in_package is None and kind is not None:
        sql += " ORDER BY name"

    rows = conn.execute(sql, params).fetchall()
    return [_row_to_node(r) for r in rows]


def _parse_path_line(selector: str) -> tuple[str, int] | None:
    """Parse a `path:line` selector. Returns (path, line) when the part after
    the final colon is all digits, else None (treat selector as a name).

    `builtin:`-prefixed selectors are handled by the CLI fast path before
    resolve_selector is reached, so a non-digit suffix here is always a name.
    """
    if ":" not in selector:
        return None
    path, _, tail = selector.rpartition(":")
    if path and tail.isdigit():
        return path, int(tail)
    return None


def resolve_selector(
    conn: sqlite3.Connection,
    *,
    selector: str,
    in_package: str | None = None,
) -> list[NodeRecord]:
    """find-style resolution of a describe selector across all node kinds.

    A `path:line` selector returns exactly the node(s) at that file location
    (and ignores `in_package`, since path:line is already a unique address).
    Otherwise delegates to `find(name=selector, in_package=in_package)`, which
    searches every kind by name. `conn` must be opened read-only.
    """
    parsed = _parse_path_line(selector)
    if parsed is not None:
        path, line = parsed
        rows = conn.execute(
            "SELECT kind, name, path, line, attrs_json FROM nodes WHERE path = ? AND line = ?",
            (path, line),
        ).fetchall()
        return [_row_to_node(r) for r in rows]
    return find(conn, name=selector, in_package=in_package)


def build_menu(conn: sqlite3.Connection, matches: list[NodeRecord]) -> list[MatchRecord]:
    """Enrich resolve_selector matches into copy-paste disambiguation entries.

    Keys command synthesis on node KIND so every emitted `gw graph describe`
    command round-trips (resolves when pasted back):
      * code symbol      -> `... <path>:<line>` on a same-package collision or
                            when there is no containing package/app to narrow by;
                            otherwise `... <name> --kind <cli> --in-package <pkg>`
      * file             -> `... <path>` (resolves via the path describer)
      * dependency       -> `... <org>/<repo>/<ecosystem>/<name> --kind dependency`
                            (dependency nodes carry a synthetic path; never
                            address them by `--in-package`)
      * other path-less  -> `... <name> --kind <cli>`
    `conn` read-only.
    """
    packages = [containing_package(conn, path=m.path) if m.path else None for m in matches]
    # Group key (kind, name, package) -> count, to flag collisions.
    counts: dict[tuple[str, str, str | None], int] = {}
    for m, pkg in zip(matches, packages, strict=False):
        key = (m.kind, m.name, pkg)
        counts[key] = counts.get(key, 0) + 1

    out: list[MatchRecord] = []
    for m, pkg in zip(matches, packages, strict=False):
        # Menu label location: only code symbols have a line; line-less nodes
        # (packages, deps, files, …) render a blank address (no ":None").
        address = f"{m.path}:{m.line}" if m.line is not None else ""
        cli_kind = _CLI_KIND.get(m.kind, m.kind)
        collides = counts[(m.kind, m.name, pkg)] > 1
        if m.kind in _CODE_KINDS:
            # Unambiguous path:line when a same-name/kind/package collision can't
            # be broken by --in-package, or when the symbol has no containing
            # package/app to narrow by; otherwise name + --in-package.
            if collides or pkg is None:
                command = f"gw graph describe {m.path}:{m.line}"
            else:
                command = f"gw graph describe {m.name} --kind {cli_kind} --in-package {pkg}"
        elif m.kind == "builtin":
            # Builtin nodes key on (name=module, path=language); describe takes
            # the two folded into one `builtin:<language>/<module>` identifier.
            command = f"gw graph describe builtin:{m.path}/{m.name} --kind builtin"
        elif m.kind == "file":
            # A bare file path resolves via q_describe.run's path-describer fallback.
            command = f"gw graph describe {m.path}"
        elif m.kind == "dependency":
            # Dependency nodes carry a synthetic, repository-scoped path; the
            # describe identifier is `<org>/<repo>/<ecosystem>/<name>`.
            identifier = dependency_identifier_from_path(m.path or "") or m.name
            command = f"gw graph describe {identifier} --kind dependency"
        else:
            # Path-less entities (package, app, test_suite, agent_plugin,
            # entry_point) resolve by name under their explicit kind.
            command = f"gw graph describe {m.name} --kind {cli_kind}"
        out.append(MatchRecord(kind=m.kind, address=address, command=command))
    return out


def containing_package(conn: sqlite3.Connection, *, path: str) -> str | None:
    """Return the name of the package OR app whose `contains`-edge owns `path`, or None.

    Reverse of find's `--in-package` join (package -> file), broadened to also
    match `app` nodes so app-resident symbols resolve to their owning app
    (otherwise build_menu would emit `--in-package None`). Path must match the
    stored form exactly — no normalisation/casing is applied (unlike find's
    `in_package`, which lowercases the package name). `conn` must be opened
    read-only.
    """
    row = conn.execute(
        "SELECT p.name FROM nodes p "
        "JOIN edges ce ON ce.src = p.id AND ce.kind='contains' "
        "JOIN nodes f ON ce.dst = f.id AND f.kind='file' "
        "WHERE p.kind IN ('package', 'app') AND f.path = ? "
        "LIMIT 1",
        (path,),
    ).fetchone()
    return row[0] if row else None


def describe_symbol(
    conn: sqlite3.Connection,
    *,
    kind: str,
    name: str,
    in_package: str | None = None,
    path: str | None = None,
    line: int | None = None,
) -> SymbolDescription | None:
    """Locate the first matching node (ORDER BY path, line) and assemble a fixed-depth-1 dossier.

    Returns None if no node matches. Supply `path` and `line` to pin an exact
    node; omit them to accept the first match. `kind` is the DB node kind
    (function/class/method/type). `conn` must be opened read-only.

    The dossier's `callers`/`callees` intentionally exclude test-file symbols:
    per decision D4, `describe` inherits the default-exclude and has no opt-in.
    To see test-file callers/callees, use `gw graph callers <name>
    --include-tests` directly.
    """
    where = ["kind = ?", "(name = ? OR name LIKE '%.' || ?)"]
    params: SqlParams = [kind, name, name]
    if path is not None:
        where.append("path = ?")
        params.append(path)
    if line is not None:
        where.append("line = ?")
        params.append(line)
    if in_package is not None:
        where.append(f"path IN {_FILES_IN_PACKAGE_SUBQUERY}")
        params.append(in_package)
    row = conn.execute(
        "SELECT kind, name, path, line, attrs_json FROM nodes WHERE "
        + " AND ".join(where)
        + " ORDER BY path, line LIMIT 1",
        params,
    ).fetchone()
    if row is None:
        return None
    db_kind, db_name, node_path, node_line, attrs_json = row
    node_attrs = json.loads(attrs_json) if attrs_json else {}

    package = containing_package(conn, path=node_path) if node_path else None
    exporters = exported_by(conn, name=db_name)
    exported_from = exporters[0].path if exporters else None
    return SymbolDescription(
        kind=db_kind,
        name=db_name,
        path=node_path,
        line=node_line,
        package=package,
        exported_from=exported_from,
        token_count=node_attrs.get("token_count"),
        callers=callers(conn, name=db_name, depth=1),
        callees=callees(conn, name=db_name, depth=1),
    )


def callers(
    conn: sqlite3.Connection,
    *,
    name: str,
    depth: int = 3,
    include_test_files: bool = False,
) -> list[CallRecord]:
    # D2 prune: when include_test_files is False, cut test-file callers out of
    # the walk (base AND recursive), severing paths that reach `name` only
    # through a test node. When True, BOTH the candidate JOIN and the filter
    # are omitted, so the opt-in query is structurally identical to the
    # pre-flag query (no extra JOIN that could alter results).
    if include_test_files:
        src_join = ""
        src_filter = ""
    else:
        src_join = "JOIN nodes src ON e.src = src.id"
        src_filter = f" AND {_NON_TEST_FILTER.format(alias='src')}"
    rows = conn.execute(
        f"""
        WITH RECURSIVE c(id, depth) AS (
            SELECT e.src, 1 FROM edges e
            JOIN nodes target ON e.dst = target.id
            {src_join}
            WHERE e.kind='calls' AND (target.name = ? OR target.name LIKE '%.' || ?) AND target.path IS NOT NULL
              AND {_RESOLVED_FILTER}{src_filter}
            UNION
            SELECT e.src, c.depth + 1 FROM edges e
            JOIN c ON e.dst = c.id
            {src_join}
            WHERE e.kind='calls' AND c.depth < ? AND {_RESOLVED_FILTER}{src_filter}
        )
        SELECT n.name, n.path, n.line, MIN(c.depth)
        FROM c JOIN nodes n ON c.id = n.id
        WHERE n.path IS NOT NULL
        GROUP BY n.id
        ORDER BY MIN(c.depth), n.name
        """,
        (name, name, depth),
    ).fetchall()
    return [CallRecord(name=r[0], path=r[1], line=r[2], depth=r[3]) for r in rows]


def callees(
    conn: sqlite3.Connection,
    *,
    name: str,
    depth: int = 3,
    include_test_files: bool = False,
) -> list[CallRecord]:
    # D2 prune: walk e.dst (the callee); cut test-file callees out of the walk
    # in both cases when include_test_files is False. When True, BOTH the dst
    # JOIN and the filter are omitted (see callers() for rationale).
    # Note: the base arm already JOINs `src` (for `src.name = ?`); `dst_join`
    # adds a distinct `dst` alias that the filter reads `dst.path` from.
    if include_test_files:
        dst_join = ""
        dst_filter = ""
    else:
        dst_join = "JOIN nodes dst ON e.dst = dst.id"
        dst_filter = f" AND {_NON_TEST_FILTER.format(alias='dst')}"
    rows = conn.execute(
        f"""
        WITH RECURSIVE c(id, depth) AS (
            SELECT e.dst, 1 FROM edges e
            JOIN nodes src ON e.src = src.id
            {dst_join}
            WHERE e.kind='calls' AND (src.name = ? OR src.name LIKE '%.' || ?) AND src.path IS NOT NULL
              AND {_RESOLVED_FILTER}{dst_filter}
            UNION
            SELECT e.dst, c.depth + 1 FROM edges e
            JOIN c ON e.src = c.id
            {dst_join}
            WHERE e.kind='calls' AND c.depth < ? AND {_RESOLVED_FILTER}{dst_filter}
        )
        SELECT n.name, n.path, n.line, MIN(c.depth)
        FROM c JOIN nodes n ON c.id = n.id
        WHERE n.path IS NOT NULL
        GROUP BY n.id
        ORDER BY MIN(c.depth), n.name
        """,
        (name, name, depth),
    ).fetchall()
    return [CallRecord(name=r[0], path=r[1], line=r[2], depth=r[3]) for r in rows]


def imports(conn: sqlite3.Connection, *, path: str) -> list[ImportRecord]:
    rows = conn.execute(
        f"""
        SELECT n.name, n.path FROM edges e
        JOIN nodes src ON e.src = src.id
        JOIN nodes n ON e.dst = n.id
        WHERE src.path = ? AND e.kind='imports' AND n.path IS NOT NULL
          AND {_RESOLVED_FILTER}
        """,
        (path,),
    ).fetchall()
    return [ImportRecord(name=r[0], path=r[1]) for r in rows]


def describe_package(
    conn: sqlite3.Connection,
    *,
    name: str,
    uri: str | None = None,
) -> PackageDescription | None:
    """Return a package description, optionally scoped to its stable URI.

    Name-only callers retain the historical lookup and aggregation semantics.
    Multi-repository callers should pass ``uri`` so every related query is
    constrained to the selected package node.
    """
    if uri is None:
        pkg = conn.execute(
            "SELECT id, attrs_json FROM nodes WHERE kind='package' AND name = ?",
            (name,),
        ).fetchone()
    else:
        pkg = conn.execute(
            "SELECT id, attrs_json FROM nodes WHERE kind='package' AND name = ? AND uri = ?",
            (name, uri),
        ).fetchone()
    if not pkg:
        return None
    package_id, attrs_json = pkg
    attrs = json.loads(attrs_json) if attrs_json else {}
    package_filter = "p.name = ?" if uri is None else "p.id = ?"
    package_filter_value: str | int = name if uri is None else package_id
    files = conn.execute(
        "SELECT n.id, n.path FROM edges e "
        "JOIN nodes p ON e.src = p.id JOIN nodes n ON e.dst = n.id "
        f"WHERE p.kind='package' AND {package_filter} AND e.kind='contains' AND n.kind='file' "
        "ORDER BY n.path",
        (package_filter_value,),
    ).fetchall()
    file_ids = [row[0] for row in files]
    file_paths = [row[1] for row in files]
    counts: dict[str, int] = {}
    if file_paths:
        if uri is None:
            placeholders = ",".join("?" for _ in file_paths)
            rows = conn.execute(
                f"SELECT kind, COUNT(*) FROM nodes WHERE path IN ({placeholders}) AND kind != 'file' GROUP BY kind",
                file_paths,
            ).fetchall()
        else:
            placeholders = ",".join("?" for _ in file_ids)
            rows = conn.execute(
                f"SELECT n.kind, COUNT(*) FROM nodes n "
                "WHERE n.kind != 'file' AND EXISTS ("
                f"SELECT 1 FROM nodes f WHERE f.id IN ({placeholders}) "
                "AND f.path = n.path AND f.repo IS n.repo) "
                "GROUP BY n.kind",
                file_ids,
            ).fetchall()
        counts = {kind: count for kind, count in rows}

    # EntryPoints declared by the package
    ep_rows = conn.execute(
        "SELECT ep.name, ep.uri, ep.attrs_json, f.path "
        "FROM nodes pkg "
        "JOIN edges de ON de.src = pkg.id AND de.kind='declares_entry_point' "
        "JOIN nodes ep ON ep.id = de.dst AND ep.kind='entry_point' "
        "LEFT JOIN edges ib ON ib.src = ep.id AND ib.kind='implemented_by' "
        "LEFT JOIN nodes f ON f.id = ib.dst AND f.kind='file' "
        f"WHERE pkg.kind='package' AND {'pkg.name = ?' if uri is None else 'pkg.id = ?'} "
        "ORDER BY ep.name",
        (package_filter_value,),
    ).fetchall()
    entry_points = [_load_entry_point_description(r) for r in ep_rows]

    # TestSuites covering the package
    suite_rows = conn.execute(
        "SELECT ts.name, ts.uri, ts.attrs_json, "
        "(SELECT COUNT(*) FROM edges pc "
        " WHERE pc.src = ts.id AND pc.kind='physically_contains') AS fc "
        "FROM edges t "
        "JOIN nodes ts ON t.src = ts.id "
        "JOIN nodes p ON t.dst = p.id "
        "WHERE t.kind='tests' AND ts.kind='test_suite' "
        f"AND p.kind='package' AND {package_filter} "
        "ORDER BY ts.name",
        (package_filter_value,),
    ).fetchall()
    test_suites = [_load_suite_description(r) for r in suite_rows]

    # both directions of the depends_on_package edge.
    # Internal DEPENDENCIES (outgoing): workspace packages this one depends on —
    # dst names of edges whose src is this package.
    internal_dep_rows = conn.execute(
        "SELECT dst.name FROM edges e "
        "JOIN nodes src ON e.src = src.id "
        "JOIN nodes dst ON e.dst = dst.id "
        "WHERE e.kind='depends_on_package' "
        f"AND src.kind IN ('package', 'app') AND {'src.name = ?' if uri is None else 'src.id = ?'} "
        "AND dst.kind IN ('package', 'app') "
        "ORDER BY dst.name",
        (package_filter_value,),
    ).fetchall()
    internal_dependencies = [r[0] for r in internal_dep_rows]
    # Internal DEPENDENTS (incoming): workspace packages that depend on this one —
    # src names of edges whose dst is this package.
    internal_dependent_rows = conn.execute(
        "SELECT src.name FROM edges e "
        "JOIN nodes src ON e.src = src.id "
        "JOIN nodes dst ON e.dst = dst.id "
        "WHERE e.kind='depends_on_package' "
        f"AND dst.kind IN ('package', 'app') AND {'dst.name = ?' if uri is None else 'dst.id = ?'} "
        "AND src.kind IN ('package', 'app') "
        "ORDER BY src.name",
        (package_filter_value,),
    ).fetchall()
    internal_dependents = [r[0] for r in internal_dependent_rows]

    # Facts aggregated over every repository-scoped Dependency node that is
    # implemented_by this package (D-004): the "who else uses X" query. Same
    # consumer-kind filter and ordering as describe_dependency. An unconsumed
    # package has no such node, so both lists stay empty.
    implemented_rows = conn.execute(
        "SELECT dep.id, dep.attrs_json FROM edges e "
        "JOIN nodes dep ON e.src = dep.id "
        "WHERE e.kind='implemented_by' AND e.dst = ? AND dep.kind='dependency' "
        "ORDER BY dep.uri",
        (package_id,),
    ).fetchall()
    consumer_uris: set[str] = set()
    version_entries: set[str] = set()
    for dep_id, dep_attrs_json in implemented_rows:
        consumer_uris.update(
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT p.uri FROM edges e "
                "JOIN nodes p ON e.src = p.id "
                "WHERE e.kind='used_by' AND e.dst = ? AND p.kind IN ('package', 'app', 'repository') "
                "AND p.uri IS NOT NULL",
                (dep_id,),
            ).fetchall()
        )
        dep_attrs = json.loads(dep_attrs_json) if dep_attrs_json else {}
        versions = dep_attrs.get("versions_in_use") or []
        if isinstance(versions, list):
            version_entries.update(str(v) for v in versions)
    used_by = sorted(consumer_uris)
    versions_in_use = sorted(version_entries)

    return PackageDescription(
        name=name,
        language=attrs.get("language", ""),
        version=attrs.get("version", ""),
        files=file_paths,
        counts=counts,
        entry_points=entry_points,
        test_suites=test_suites,
        internal_dependencies=internal_dependencies,
        internal_dependents=internal_dependents,
        used_by=used_by,
        versions_in_use=versions_in_use,
    )


def internal_dependencies_of(conn: sqlite3.Connection, *, name: str) -> list[str]:
    """Outgoing `depends_on_package` dst-names for the node named `name`.

    Returns the workspace packages/apps that `name` depends on, sorted
    alphabetically. Unlike `describe_package` (which gates on `kind='package'`
    and returns None for apps), this works for a `name` of kind `package` OR
    `app` because it does not filter on the source node's own kind beyond
    `src.kind IN ('package', 'app')`. This is the single source of
    internal-dependency truth reused by wiki-io's index generator for both
    packages and apps — wiki-io must NOT write parallel SQL.

    A node with no outgoing `depends_on_package` edges (or a non-existent
    name) returns `[]`. `?` placeholder only; read-only.
    """
    rows = conn.execute(
        "SELECT dst.name FROM edges e "
        "JOIN nodes src ON e.src = src.id "
        "JOIN nodes dst ON e.dst = dst.id "
        "WHERE e.kind='depends_on_package' "
        "AND src.kind IN ('package', 'app') AND src.name = ? "
        "AND dst.kind IN ('package', 'app') "
        "ORDER BY dst.name",
        (name,),
    ).fetchall()
    return [r[0] for r in rows]


def internal_dependency_uris_of(conn: sqlite3.Connection, *, uri: str) -> list[str]:
    """Repo-scoped `package`/`app` URIs the node with *uri* depends on, same repo only.

    `internal_dependencies_of` keys off a bare `src.name` with no repository
    filter, so a caller resolving its dst-names back to URIs through a
    name-keyed index (e.g. an `affects` closure scoped to one repository) can
    pick up a same-named package's dependencies from a *different*
    repository. This query follows outgoing `depends_on_package` edges from
    the `package`/`app` node whose `uri` is *uri* directly to `package`/`app`
    destination nodes, keeping only destinations in the same repository as
    the source (`dst.repo = src.repo`) so cross-repository leakage can't
    happen structurally. Sorted, distinct; nodes with no `uri` are dropped;
    `[]` for an unknown URI. `?` placeholder only; read-only.
    """
    rows = conn.execute(
        "SELECT DISTINCT dst.uri FROM edges e "
        "JOIN nodes src ON e.src = src.id "
        "JOIN nodes dst ON e.dst = dst.id "
        "WHERE e.kind='depends_on_package' AND src.kind IN ('package', 'app') AND src.uri = ? "
        "AND dst.kind IN ('package', 'app') AND dst.uri IS NOT NULL AND dst.repo = src.repo "
        "ORDER BY dst.uri",
        (uri,),
    ).fetchall()
    return [str(r[0]) for r in rows]


def external_dependencies_of(conn: sqlite3.Connection, *, uri: str) -> list[str]:
    """Repo-scoped `dependency:` URIs the package or app with *uri* uses.

    Follows outgoing `used_by` edges from the `package`/`app` node whose `uri`
    is *uri* to `dependency`-kind nodes, keeping only dependencies scoped to
    the source's own repository — structurally, `dep.repo = src.repo` with
    `dep.repo` non-NULL, never by the URI's shape. Legacy unscoped
    `dependency:{ecosystem}/{name}` nodes carry a NULL `repo`, so they are
    dropped however many `/` their name holds (`dependency:go/github.com/x/y`);
    another repository's scoped dependency is dropped too. Builtins share the
    edge kind but not the node kind, so they never match. Sorted, distinct;
    `[]` for an unknown URI. `?` placeholder only; read-only. The one forward
    package → external-dependency query — callers must not compose it from
    `consumer_packages`.
    """
    rows = conn.execute(
        "SELECT DISTINCT dep.uri FROM edges e "
        "JOIN nodes src ON e.src = src.id "
        "JOIN nodes dep ON e.dst = dep.id "
        "WHERE e.kind='used_by' AND src.kind IN ('package', 'app') AND src.uri = ? "
        "AND dep.kind='dependency' AND dep.uri IS NOT NULL "
        "AND dep.repo IS NOT NULL AND dep.repo = src.repo "
        "ORDER BY dep.uri",
        (uri,),
    ).fetchall()
    return [str(r[0]) for r in rows]


def describe_app(
    conn: sqlite3.Connection,
    *,
    name: str,
    uri: str | None = None,
) -> AppDescription | None:
    """Return the named App's description, or None.

    The node lookup itself reads `kind='app'` — an App's own attrs
    (`app_kind`, `app_signals`, `language`, `version`) live only on the App
    node. Everything else (files/entry_points/test_suites) is sourced from
    the sibling Package node of the same name: under the facet model, a
    Package node is created unconditionally for every manifest-bearing
    member, and an App is an ADDITIONAL facet linked `Package
    --facet_of--> App` — containment/entry-point/test edges source
    exclusively from the Package side, never from the App node. `conn` must
    be opened read-only.
    """
    if uri is None:
        app = conn.execute(
            "SELECT id, attrs_json FROM nodes WHERE kind='app' AND name = ?",
            (name,),
        ).fetchone()
    else:
        app = conn.execute(
            "SELECT id, attrs_json FROM nodes WHERE kind='app' AND name = ? AND uri = ?",
            (name, uri),
        ).fetchone()
    if not app:
        return None
    app_id, attrs_json = app
    attrs = json.loads(attrs_json) if attrs_json else {}
    sibling_package_id: int | None = None
    if uri is not None:
        sibling = conn.execute(
            "SELECT pkg.id FROM edges facet "
            "JOIN nodes pkg ON facet.src = pkg.id "
            "WHERE facet.kind='facet_of' AND facet.dst = ? AND pkg.kind='package' "
            "ORDER BY pkg.id LIMIT 1",
            (app_id,),
        ).fetchone()
        sibling_package_id = sibling[0] if sibling else None

    package_filter = "p.name = ?" if uri is None else "p.id = ?"
    package_filter_value: str | int | None = name if uri is None else sibling_package_id
    # contains edges always source from the Package node under the facet
    # model — an App is always faceted off a Package of the same name, so
    # this still resolves to the right member's files.
    files = []
    if package_filter_value is not None:
        files = conn.execute(
            "SELECT n.id, n.path FROM edges e "
            "JOIN nodes p ON e.src = p.id JOIN nodes n ON e.dst = n.id "
            f"WHERE p.kind='package' AND {package_filter} AND e.kind='contains' AND n.kind='file' "
            "ORDER BY n.path",
            (package_filter_value,),
        ).fetchall()
    file_ids = [row[0] for row in files]
    file_paths = [row[1] for row in files]
    counts: dict[str, int] = {}
    if file_paths:
        if uri is None:
            placeholders = ",".join("?" for _ in file_paths)
            rows = conn.execute(
                f"SELECT kind, COUNT(*) FROM nodes WHERE path IN ({placeholders}) AND kind != 'file' GROUP BY kind",
                file_paths,
            ).fetchall()
        else:
            placeholders = ",".join("?" for _ in file_ids)
            rows = conn.execute(
                f"SELECT n.kind, COUNT(*) FROM nodes n "
                "WHERE n.kind != 'file' AND EXISTS ("
                f"SELECT 1 FROM nodes f WHERE f.id IN ({placeholders}) "
                "AND f.path = n.path AND f.repo IS n.repo) "
                "GROUP BY n.kind",
                file_ids,
            ).fetchall()
        counts = {kind: count for kind, count in rows}

    # EntryPoints declared by the sibling Package node (facet model: the App
    # node never declares this edge itself).
    ep_rows = []
    if package_filter_value is not None:
        ep_rows = conn.execute(
            "SELECT ep.name, ep.uri, ep.attrs_json, f.path "
            "FROM nodes pkg "
            "JOIN edges de ON de.src = pkg.id AND de.kind='declares_entry_point' "
            "JOIN nodes ep ON ep.id = de.dst AND ep.kind='entry_point' "
            "LEFT JOIN edges ib ON ib.src = ep.id AND ib.kind='implemented_by' "
            "LEFT JOIN nodes f ON f.id = ib.dst AND f.kind='file' "
            f"WHERE pkg.kind = 'package' AND {'pkg.name = ?' if uri is None else 'pkg.id = ?'} "
            "ORDER BY ep.name",
            (package_filter_value,),
        ).fetchall()
    entry_points = [_load_entry_point_description(r) for r in ep_rows]

    # TestSuites covering the sibling Package node (facet model).
    suite_rows = []
    if package_filter_value is not None:
        suite_rows = conn.execute(
            "SELECT ts.name, ts.uri, ts.attrs_json, "
            "(SELECT COUNT(*) FROM edges pc "
            " WHERE pc.src = ts.id AND pc.kind='physically_contains') AS fc "
            "FROM edges t "
            "JOIN nodes ts ON t.src = ts.id "
            "JOIN nodes p ON t.dst = p.id "
            "WHERE t.kind='tests' AND ts.kind='test_suite' "
            f"AND p.kind='package' AND {package_filter} "
            "ORDER BY ts.name",
            (package_filter_value,),
        ).fetchall()
    test_suites = [_load_suite_description(r) for r in suite_rows]

    return AppDescription(
        name=name,
        language=attrs.get("language", ""),
        version=attrs.get("version", ""),
        app_kind=attrs.get("app_kind", ""),
        app_signals=list(attrs.get("app_signals") or []),
        files=file_paths,
        counts=counts,
        entry_points=entry_points,
        test_suites=test_suites,
    )


def describe_path(conn: sqlite3.Connection, *, path: str) -> PathDescription | None:
    file_row = conn.execute(
        "SELECT kind, name, path, line, attrs_json FROM nodes WHERE kind='file' AND path = ?",
        (path,),
    ).fetchone()
    if not file_row:
        return None
    children_rows = conn.execute(
        f"""
        SELECT n.kind, n.name, n.path, n.line, n.attrs_json FROM edges e
        JOIN nodes src ON e.src = src.id
        JOIN nodes n ON e.dst = n.id
        WHERE src.kind='file' AND src.path = ? AND e.kind='contains'
          AND {_RESOLVED_FILTER}
        ORDER BY n.line
        """,
        (path,),
    ).fetchall()
    import_rows = conn.execute(
        f"""
        SELECT n.kind, n.name, n.path, n.line, n.attrs_json FROM edges e
        JOIN nodes src ON e.src = src.id
        JOIN nodes n ON e.dst = n.id
        WHERE src.kind='file' AND src.path = ? AND e.kind='imports'
          AND n.path IS NOT NULL AND {_RESOLVED_FILTER}
        ORDER BY n.path
        """,
        (path,),
    ).fetchall()
    # Project the 7 File role flags into a dict
    file_attrs = json.loads(file_row[4]) if file_row[4] else {}
    role_flags: dict[str, bool] | None = {
        "is_importable": bool(file_attrs.get("is_importable", False)),
        "has_main": bool(file_attrs.get("has_main", False)),
        "is_test": bool(file_attrs.get("is_test", False)),
        "is_config": bool(file_attrs.get("is_config", False)),
        "is_generated": bool(file_attrs.get("is_generated", False)),
        "is_type_only": bool(file_attrs.get("is_type_only", False)),
        "is_executable": bool(file_attrs.get("is_executable", False)),
    }
    export_records = exports(conn, path=path)
    return PathDescription(
        path=path,
        children=[_row_to_node(r) for r in children_rows],
        imports=[_row_to_node(r) for r in import_rows],
        role_flags=role_flags,
        token_count=file_attrs.get("token_count"),
        exports=export_records,
    )


def describe_repository(conn: sqlite3.Connection) -> RepoDescription | None:
    """Return the single Repository node's description, or None if absent.

    guarantees exactly one Repository per DB. `conn` must
    be a `sqlite3.Connection` opened with `mode=ro`.
    """
    row = conn.execute("SELECT name, uri, attrs_json FROM nodes WHERE kind='repository' LIMIT 1").fetchone()
    if not row:
        return None
    name, uri, attrs_json = row
    attrs = json.loads(attrs_json) if attrs_json else {}
    pkg_count = conn.execute("SELECT COUNT(*) FROM nodes WHERE kind='package'").fetchone()[0]
    return RepoDescription(
        name=name,
        uri=uri or "",
        owner=attrs.get("owner"),
        url=attrs.get("url"),
        default_branch=attrs.get("default_branch"),
        package_count=int(pkg_count or 0),
    )


def describe_entry_point(
    conn: sqlite3.Connection,
    *,
    package_name: str,
    entry_name: str,
) -> EntryPointDescription | None:
    """Return the named EntryPoint declared by the package, or None.

    `conn` must be a `sqlite3.Connection` opened with `mode=ro`.
    """
    row = conn.execute(
        "SELECT ep.name, ep.uri, ep.attrs_json, f.path "
        "FROM nodes pkg "
        "JOIN edges de ON de.src = pkg.id AND de.kind='declares_entry_point' "
        "JOIN nodes ep ON ep.id = de.dst AND ep.kind='entry_point' "
        "LEFT JOIN edges ib ON ib.src = ep.id AND ib.kind='implemented_by' "
        "LEFT JOIN nodes f ON f.id = ib.dst AND f.kind='file' "
        # apps declare entry points the same way packages do.
        "WHERE pkg.kind IN ('package', 'app') AND pkg.name = ? AND ep.name = ?",
        (package_name, entry_name),
    ).fetchone()
    if not row:
        return None
    return _load_entry_point_description(row)


# Container kinds show one level of containment by default; structural/symbol
# kinds show two so the first describe already reaches into the symbol level.
_CONTAINER_KINDS = frozenset({"repository", "package", "app"})


def default_child_depth(kind: str) -> int:
    """Effective `children` depth used when `--depth` is omitted (Design 8)."""
    return 1 if kind in _CONTAINER_KINDS else 2


def _node_id(conn: sqlite3.Connection, node: NodeRecord) -> int | None:
    """Resolve the nodes.id for a describe target, most-specific identity first.

    uri (if carried in attrs) -> (kind, path, line) -> (kind, path) -> (kind, name).
    Returns None when no row matches (caller yields an empty tree).
    """
    uri = (node.attrs or {}).get("uri")
    if uri:
        row = conn.execute("SELECT id FROM nodes WHERE uri = ?", (uri,)).fetchone()
        if row:
            return int(row[0])
    if node.path is not None and node.line is not None:
        row = conn.execute(
            "SELECT id FROM nodes WHERE kind = ? AND path = ? AND line = ?",
            (node.kind, node.path, node.line),
        ).fetchone()
        if row:
            return int(row[0])
    if node.path is not None:
        row = conn.execute("SELECT id FROM nodes WHERE kind = ? AND path = ?", (node.kind, node.path)).fetchone()
        if row:
            return int(row[0])
    row = conn.execute("SELECT id FROM nodes WHERE kind = ? AND name = ? LIMIT 1", (node.kind, node.name)).fetchone()
    return int(row[0]) if row else None


# Outgoing physically_contains, filtered to the given dst kinds, ordered
# subpackage-before-file then by path. `{dst}` is an inlined IN-list of literals
# from a fixed allow-list (never user input).
def _phys_children_sql(dst_kinds: tuple[str, ...]) -> str:
    in_list = ",".join(f"'{k}'" for k in dst_kinds)
    return (
        "SELECT n.id, n.kind, n.uri, n.path, n.line, n.name "
        "FROM edges e JOIN nodes n ON e.dst = n.id "
        f"WHERE e.src = ? AND e.kind = 'physically_contains' AND n.kind IN ({in_list}) "
        "ORDER BY CASE n.kind WHEN 'subpackage' THEN 0 ELSE 1 END, n.path"
    )


def _contains_symbols_sql(dst_kinds: tuple[str, ...]) -> str:
    in_list = ",".join(f"'{k}'" for k in dst_kinds)
    return (
        "SELECT n.id, n.kind, n.uri, n.path, n.line, n.name "
        "FROM edges e JOIN nodes n ON e.dst = n.id "
        f"WHERE e.src = ? AND e.kind = 'contains' AND n.kind IN ({in_list}) "
        f"AND {_RESOLVED_FILTER} "
        "ORDER BY n.line"
    )


_TEST_SUITES_SQL = (
    "SELECT ts.id, ts.kind, ts.uri, ts.path, ts.line, ts.name "
    "FROM edges e JOIN nodes ts ON e.src = ts.id "
    "WHERE e.dst = ? AND e.kind = 'tests' AND ts.kind = 'test_suite' "
    "ORDER BY ts.name"
)

_ENTRY_POINTS_SQL = (
    "SELECT ep.id, ep.kind, ep.uri, ep.path, ep.line, ep.name "
    "FROM edges e JOIN nodes ep ON e.dst = ep.id "
    "WHERE e.src = ? AND e.kind = 'declares_entry_point' AND ep.kind = 'entry_point' "
    "ORDER BY ep.name"
)


def _direct_children(
    conn: sqlite3.Connection, node_id: int, kind: str
) -> list[tuple[int, str, str | None, str | None, int | None, str | None]]:
    """Kind-dispatched direct children as raw rows (id, kind, uri, path, line, name).

    Container/structural kinds descend `physically_contains`; symbol nesting
    descends `contains`; test_suites/entry_points come from their dedicated
    edges. External deps are intentionally excluded (they live in the
    `relationships` section). Returns [] for leaf kinds.
    """
    if kind == "repository":
        return conn.execute(_phys_children_sql(("package", "app")), (node_id,)).fetchall()
    if kind in ("package", "app"):
        rows = list(conn.execute(_phys_children_sql(("subpackage", "file")), (node_id,)).fetchall())
        rows += conn.execute(_TEST_SUITES_SQL, (node_id,)).fetchall()
        rows += conn.execute(_ENTRY_POINTS_SQL, (node_id,)).fetchall()
        return rows
    if kind == "subpackage":
        return conn.execute(_phys_children_sql(("subpackage", "file")), (node_id,)).fetchall()
    if kind == "test_suite":
        return conn.execute(_phys_children_sql(("file",)), (node_id,)).fetchall()
    if kind == "file":
        return conn.execute(_contains_symbols_sql(("function", "class", "method", "type")), (node_id,)).fetchall()
    if kind == "class":
        return conn.execute(_contains_symbols_sql(("method",)), (node_id,)).fetchall()
    if kind in ("function", "method"):
        return conn.execute(_contains_symbols_sql(("function",)), (node_id,)).fetchall()
    return []  # type / dependency / builtin / entry_point / unresolved_symbol -> leaf


def _expand(conn: sqlite3.Connection, node_id: int, kind: str, depth: int, visited: set[int]) -> list[ChildNode]:
    if depth <= 0 or node_id in visited:
        return []
    visited = visited | {node_id}
    out: list[ChildNode] = []
    for child_id, child_kind, uri, path, line, name in _direct_children(conn, node_id, kind):
        grandchildren = _expand(conn, child_id, child_kind, depth - 1, visited)
        out.append(ChildNode(kind=child_kind, uri=uri, path=path, line=line, name=name, children=grandchildren))
    return out


def children_tree(conn: sqlite3.Connection, *, node: NodeRecord, depth: int) -> list[ChildNode]:
    """Structural containment tree for `node`, bounded to `depth` nesting levels.

    depth=1 yields direct children only. Each child expands by ITS OWN kind's
    rule. A `visited` set is a cycle backstop (structural containment is acyclic
    in practice). Read-only; `conn` must be opened `mode=ro`.
    """
    node_id = _node_id(conn, node)
    if node_id is None:
        return []
    return _expand(conn, node_id, node.kind, depth, set())


def children_for(
    conn: sqlite3.Connection,
    *,
    kind: str,
    name: str | None = None,
    path: str | None = None,
    line: int | None = None,
    uri: str | None = None,
    depth: int | None,
) -> tuple[list[ChildNode], int]:
    """Surface convenience: resolve the effective depth (per-kind default when
    `depth is None`) and return `(tree, effective_depth)`. Used by every
    describe surface so depth-defaulting cannot drift between them."""
    effective = depth if depth is not None else default_child_depth(kind)
    node = NodeRecord(kind=kind, name=name or "", path=path, line=line, attrs={"uri": uri} if uri else {})
    return children_tree(conn, node=node, depth=effective), effective


def resolve_entry_point(conn: sqlite3.Connection, raw: str) -> tuple[EntryPointDescription | None, list[str]]:
    """Resolve an entry-point selector to a description.

    Accepts a qualified ``package:entry`` form or a bare entry name (scanned
    across all packages AND apps that declare it). Returns
    ``(desc_or_None, ambiguous_packages)``: when the bare name matches >1
    package, ``desc`` is None and the list names the candidates (caller emits
    the AMBIGUOUS error). Shared by run_describe and q_describe_entry_point."""
    if ":" in raw:
        package_name, entry_name = raw.split(":", 1)
        return describe_entry_point(conn, package_name=package_name, entry_name=entry_name), []
    rows = conn.execute(
        "SELECT pkg.name "
        "FROM nodes pkg "
        "JOIN edges de ON de.src = pkg.id AND de.kind='declares_entry_point' "
        "JOIN nodes ep ON ep.id = de.dst AND ep.kind='entry_point' "
        "WHERE pkg.kind IN ('package', 'app') AND ep.name = ?",
        (raw,),
    ).fetchall()
    if not rows:
        return None, []
    if len(rows) > 1:
        return None, [r[0] for r in rows]
    return describe_entry_point(conn, package_name=rows[0][0], entry_name=raw), []


def describe_test_suite(
    conn: sqlite3.Connection,
    *,
    suite_name: str,
    uri: str | None = None,
) -> SuiteDescription | None:
    """Return the named TestSuite description, or None.

    `conn` must be a `sqlite3.Connection` opened with `mode=ro`.
    """
    if uri is None:
        row = conn.execute(
            "SELECT id, name, uri, attrs_json FROM nodes WHERE kind='test_suite' AND name = ?",
            (suite_name,),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT id, name, uri, attrs_json FROM nodes WHERE kind='test_suite' AND name = ? AND uri = ?",
            (suite_name, uri),
        ).fetchone()
    if not row:
        return None
    suite_id, name, uri, attrs_json = row
    fc = conn.execute(
        "SELECT COUNT(*) FROM edges WHERE src = ? AND kind='physically_contains'",
        (suite_id,),
    ).fetchone()[0]
    file_rows = conn.execute(
        "SELECT f.path FROM edges pc JOIN nodes f ON pc.dst = f.id "
        "WHERE pc.src = ? AND pc.kind='physically_contains' ORDER BY f.path",
        (suite_id,),
    ).fetchall()
    desc = _load_suite_description((name, uri, attrs_json, fc))
    return dataclasses.replace(desc, files=[r[0] for r in file_rows])


def describe_dependency(
    conn: sqlite3.Connection,
    *,
    uri: str | None = None,
    repo: str | None = None,
    ecosystem: str | None = None,
    name: str | None = None,
) -> DependencyDescription | None:
    """Return the description of the repository-scoped dependency node at *uri*.

    `repo` (a `repo:<org>/<repo>` URI) + `ecosystem` + `name` is an equivalent
    keyword form that composes `dependency:<org>/<repo>/<ecosystem>/<name>`;
    `name` must already be the normalized spelling the node carries.

    Reads `versions_in_use` from the node's attrs, and populates `used_by`
    from inbound `used_by` edges. Consumer-side filters broaden to
    `p.kind IN ('package', 'app', 'repository')` so App consumers of a
    dependency remain discoverable — the same convention `describe_app`'s
    docstring names — and so a virtual workspace root's dev tooling,
    re-sourced to the Repository node, renders too.

    `used_by` carries consumer **URIs**, not bare names (ADR 2026-09-07-dependencies): the three
    admitted kinds flattened into one name list cannot say which is which, so
    a virtual workspace root reads as a package. A URI is self-describing
    about kind and resolvable to a page by placement. `describe_package`'s
    migrated `used_by` uses the same representation.

    Deduplicated and sorted by consumer URI. `conn` must be opened read-only.
    """
    if uri is None:
        if repo is None or ecosystem is None or name is None:
            raise ValueError("describe_dependency needs uri, or repo + ecosystem + name")
        uri = f"dependency:{repo.removeprefix('repo:')}/{ecosystem}/{name}"
    row = conn.execute(
        "SELECT id, name, attrs_json, uri, repo FROM nodes WHERE kind='dependency' AND uri = ?",
        (uri,),
    ).fetchone()
    if not row:
        return None
    dep_id, dep_name, attrs_json, node_uri, node_repo = row
    attrs = json.loads(attrs_json) if attrs_json else {}
    used_by_rows = conn.execute(
        "SELECT DISTINCT p.uri FROM edges e "
        "JOIN nodes p ON e.src = p.id "
        "WHERE e.kind='used_by' AND e.dst = ? AND p.kind IN ('package', 'app', 'repository') "
        "AND p.uri IS NOT NULL "
        "ORDER BY p.uri",
        (dep_id,),
    ).fetchall()
    used_by = [r[0] for r in used_by_rows]
    implemented_by_rows = conn.execute(
        "SELECT DISTINCT pkg.uri FROM edges e "
        "JOIN nodes pkg ON e.dst = pkg.id "
        "WHERE e.kind='implemented_by' AND e.src = ? AND pkg.kind='package' "
        "AND pkg.uri IS NOT NULL "
        "ORDER BY pkg.uri",
        (dep_id,),
    ).fetchall()
    implemented_by = [r[0] for r in implemented_by_rows]
    versions = attrs.get("versions_in_use") or []
    if not isinstance(versions, list):
        versions = []
    return DependencyDescription(
        ecosystem=attrs.get("ecosystem", ecosystem or ""),
        name=dep_name,
        uri=node_uri or "",
        repository=node_repo or "",
        versions_in_use=list(versions),
        used_by=used_by,
        implemented_by=implemented_by,
    )


def describe_builtin(conn: sqlite3.Connection, *, language: str, module_name: str) -> BuiltinDescription | None:
    """Return the description of a Builtin node identified by (language, module_name).

    Populates `used_by` from inbound `used_by` edges. Consumer-side filters
    broaden to `p.kind IN ('package', 'app')` so App consumers of a builtin
    remain discoverable — the same convention `describe_app`'s docstring
    names. Deduplicated and sorted alphabetically by consumer name. `conn`
    must be opened read-only.

    mirrors `describe_dependency` with `language` /
    `module_name` substituting for `ecosystem` / `name`.
    """
    row = conn.execute(
        "SELECT id, name, attrs_json, uri FROM nodes WHERE kind='builtin' AND name = ? AND path = ?",
        (module_name, language),
    ).fetchone()
    if not row:
        return None
    builtin_id, _name, attrs_json, uri = row
    attrs = json.loads(attrs_json) if attrs_json else {}
    used_by_rows = conn.execute(
        "SELECT DISTINCT p.name FROM edges e "
        "JOIN nodes p ON e.src = p.id "
        "WHERE e.kind='used_by' AND e.dst = ? AND p.kind IN ('package', 'app') "
        "ORDER BY p.name",
        (builtin_id,),
    ).fetchall()
    used_by = [r[0] for r in used_by_rows]
    return BuiltinDescription(
        language=attrs.get("language", language),
        module_name=attrs.get("module_name", module_name),
        uri=uri or "",
        used_by=used_by,
    )


def describe_agent_plugin(
    conn: sqlite3.Connection,
    *,
    name: str,
    uri: str | None = None,
) -> AgentPluginDescription | None:
    """Return the description of an agent_plugin node, or None.

    `conn` must be opened read-only.
    """
    if uri is None:
        row = conn.execute(
            "SELECT id, name, attrs_json, uri FROM nodes WHERE kind='agent_plugin' AND name = ?",
            (name,),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT id, name, attrs_json, uri FROM nodes WHERE kind='agent_plugin' AND name = ? AND uri = ?",
            (name, uri),
        ).fetchone()
    if not row:
        return None
    plugin_id, plugin_name, attrs_json, plugin_uri = row
    attrs = json.loads(attrs_json) if attrs_json else {}
    comp = attrs.get("components") or {}
    # Sibling Package node under the facet model (Package --facet_of-->
    # agent_plugin) — None for a plain plugin root with no manifest.
    # Normally exactly one Package facets to a given agent_plugin. A plugin
    # root carrying both a pyproject.toml and a package.json under different
    # names would (invariant violation) produce two facet_of edges here;
    # ORDER BY makes the pick deterministic rather than SQLite-row-order
    # dependent in that case, instead of silently varying between runs.
    pkg_row = conn.execute(
        "SELECT p.name FROM edges e JOIN nodes p ON e.src = p.id "
        "JOIN nodes a ON e.dst = a.id "
        "WHERE e.kind='facet_of' AND p.kind='package' AND a.kind='agent_plugin' AND a.id = ? "
        "ORDER BY p.name LIMIT 1",
        (plugin_id,),
    ).fetchone()
    package_name = pkg_row[0] if pkg_row else None
    return AgentPluginDescription(
        name=plugin_name,
        uri=plugin_uri or "",
        ecosystem=attrs.get("ecosystem", ""),
        version=attrs.get("version", ""),
        description=attrs.get("description", ""),
        commands=list(comp.get("commands") or []),
        agents=list(comp.get("agents") or []),
        skills=list(comp.get("skills") or []),
        scripts=list(comp.get("scripts") or []),
        hooks=list(comp.get("hooks") or []),
        mcp_servers=list(comp.get("mcp_servers") or []),
        package_name=package_name,
    )


def _list_by_kind(conn: sqlite3.Connection, kind: str) -> list[NodeRecord]:
    rows = conn.execute(
        "SELECT kind, name, path, line, attrs_json, uri, repo FROM nodes WHERE kind = ? ORDER BY name",
        (kind,),
    ).fetchall()
    return [_row_to_node(r) for r in rows]


def list_repositories(conn: sqlite3.Connection) -> list[NodeRecord]:
    """List all Repository nodes alphabetically. `conn` must be read-only."""
    return _list_by_kind(conn, "repository")


def list_packages(conn: sqlite3.Connection) -> list[NodeRecord]:
    """List all Package nodes alphabetically. `conn` must be read-only."""
    return _list_by_kind(conn, "package")


def list_entry_points(conn: sqlite3.Connection) -> list[NodeRecord]:
    """List all EntryPoint nodes alphabetically. `conn` must be read-only."""
    return _list_by_kind(conn, "entry_point")


def list_test_suites(conn: sqlite3.Connection) -> list[NodeRecord]:
    """List all TestSuite nodes alphabetically. `conn` must be read-only."""
    return _list_by_kind(conn, "test_suite")


def list_dependencies(conn: sqlite3.Connection) -> list[NodeRecord]:
    """List all Dependency nodes alphabetically. `conn` must be read-only."""
    return _list_by_kind(conn, "dependency")


def list_builtins(conn: sqlite3.Connection) -> list[NodeRecord]:
    """List all Builtin nodes alphabetically. `conn` must be read-only."""
    return _list_by_kind(conn, "builtin")


def list_apps(conn: sqlite3.Connection) -> list[NodeRecord]:
    """List all App nodes alphabetically. `conn` must be read-only."""
    return _list_by_kind(conn, "app")


def list_agent_plugins(conn: sqlite3.Connection) -> list[NodeRecord]:
    """List all agent_plugin nodes alphabetically. `conn` must be read-only."""
    return _list_by_kind(conn, "agent_plugin")


def list_scripts(conn: sqlite3.Connection) -> list[NodeRecord]:
    """Union of executable Files and executable EntryPoints.

    UNION (not UNION ALL) dedups identical rows. In practice the two
    SELECTs target different `kind` columns ('file' vs 'entry_point')
    so dedup is conservative.

    Note: the emitter writes the EntryPoint kind in attrs_json under the
    `entry_kind` key (not `kind`), so we filter on that. `conn` must be
    read-only.
    """
    rows = conn.execute(
        "SELECT kind, name, path, line, attrs_json FROM nodes "
        "WHERE kind='file' "
        "AND json_extract(attrs_json, '$.is_executable') = 1 "
        "UNION "
        "SELECT kind, name, path, line, attrs_json FROM nodes "
        "WHERE kind='entry_point' "
        "AND json_extract(attrs_json, '$.entry_kind') = 'executable' "
        "ORDER BY name"
    ).fetchall()
    return [_row_to_node(r) for r in rows]


@dataclass(frozen=True)
class ImporterRecord:
    path: str
    symbols: tuple[str, ...]
    depth: int


@dataclass(frozen=True)
class ExportRecord:
    name: str
    kind: str
    line: int | None


@dataclass(frozen=True)
class ExporterRecord:
    path: str
    name: str


def imported_by(
    conn: sqlite3.Connection,
    *,
    path: str,
    symbol: str | None = None,
    depth: int = 1,
) -> list[ImporterRecord]:
    symbol_filter = "AND dst.name = ?" if symbol is not None else ""
    base_params: SqlParams = [path]
    if symbol is not None:
        base_params.append(symbol)

    if depth <= 1:
        rows = conn.execute(
            f"""
            SELECT src.path, dst.name
            FROM edges e
            JOIN nodes src ON e.src = src.id
            JOIN nodes dst ON e.dst = dst.id
            WHERE e.kind='imports' AND dst.path = ? {symbol_filter}
              AND src.path IS NOT NULL
              AND {_RESOLVED_FILTER}
            """,
            base_params,
        ).fetchall()
        grouped: dict[str, list[str]] = {}
        for src_path, sym in rows:
            grouped.setdefault(src_path, []).append(sym)
        return [ImporterRecord(path=p, symbols=tuple(sorted(syms)), depth=1) for p, syms in sorted(grouped.items())]

    rows = conn.execute(
        f"""
        WITH RECURSIVE walk(file_id, depth) AS (
            SELECT src.id, 1 FROM edges e
            JOIN nodes src ON e.src = src.id
            JOIN nodes dst ON e.dst = dst.id
            WHERE e.kind='imports' AND dst.path = ? {symbol_filter}
              AND src.path IS NOT NULL
              AND {_RESOLVED_FILTER}
            UNION
            SELECT e.src, walk.depth + 1 FROM edges e
            JOIN walk ON e.dst = walk.file_id
            WHERE e.kind='imports' AND walk.depth < ? AND {_RESOLVED_FILTER}
        )
        SELECT n.path, MIN(walk.depth)
        FROM walk JOIN nodes n ON walk.file_id = n.id
        WHERE n.path IS NOT NULL
        GROUP BY n.id
        ORDER BY MIN(walk.depth), n.path
        """,
        [*base_params, depth],
    ).fetchall()

    # Separate pass to collect symbol names for depth-1 importers: the CTE only tracks
    # (file_id, depth) and cannot carry symbol names through recursive hops.
    direct_rows = conn.execute(
        f"""
        SELECT src.path, dst.name
        FROM edges e
        JOIN nodes src ON e.src = src.id
        JOIN nodes dst ON e.dst = dst.id
        WHERE e.kind='imports' AND dst.path = ? {symbol_filter}
          AND src.path IS NOT NULL
          AND {_RESOLVED_FILTER}
        """,
        base_params,
    ).fetchall()
    direct_symbols: dict[str, list[str]] = {}
    for src_path, sym in direct_rows:
        direct_symbols.setdefault(src_path, []).append(sym)

    return [
        ImporterRecord(
            path=p,
            symbols=tuple(sorted(direct_symbols.get(p, []))),
            depth=d,
        )
        for p, d in rows
    ]


def exports(conn: sqlite3.Connection, *, path: str) -> list[ExportRecord]:
    rows = conn.execute(
        f"""
        SELECT dst.name, dst.kind, dst.line
        FROM edges e
        JOIN nodes src ON e.src = src.id
        JOIN nodes dst ON e.dst = dst.id
        WHERE e.kind='exports' AND src.path = ?
          AND {_RESOLVED_FILTER}
        ORDER BY dst.line, dst.name
        """,
        (path,),
    ).fetchall()
    return [ExportRecord(name=r[0], kind=r[1], line=r[2]) for r in rows]


def describe_file(conn: sqlite3.Connection, *, uri: str) -> FileDescription | None:
    """Describe the exact File node identified by its repository-scoped URI."""
    file_row = conn.execute(
        "SELECT id, path, attrs_json, repo, uri FROM nodes WHERE kind='file' AND uri = ? LIMIT 1",
        (uri,),
    ).fetchone()
    if file_row is None:
        return None
    file_id, path, attrs_json, repository, stored_uri = file_row
    if path is None:
        return None
    attrs = json.loads(attrs_json) if attrs_json else {}

    children_rows = conn.execute(
        f"""
        SELECT dst.kind, dst.name, dst.path, dst.line, dst.attrs_json
        FROM edges e JOIN nodes dst ON dst.id = e.dst
        WHERE e.src = ? AND e.kind='contains' AND {_RESOLVED_FILTER}
        ORDER BY dst.line, dst.name
        """,
        (file_id,),
    ).fetchall()
    import_rows = conn.execute(
        f"""
        SELECT dst.name, dst.path
        FROM edges e JOIN nodes dst ON dst.id = e.dst
        WHERE e.src = ? AND e.kind='imports' AND dst.path IS NOT NULL
          AND {_RESOLVED_FILTER}
        ORDER BY dst.path, dst.name
        """,
        (file_id,),
    ).fetchall()
    export_rows = conn.execute(
        f"""
        SELECT dst.name, dst.kind, dst.line
        FROM edges e JOIN nodes dst ON dst.id = e.dst
        WHERE e.src = ? AND e.kind='exports' AND {_RESOLVED_FILTER}
        ORDER BY dst.line, dst.name
        """,
        (file_id,),
    ).fetchall()
    package_row = conn.execute(
        "SELECT owner.name, owner.uri FROM edges e "
        "JOIN nodes owner ON owner.id = e.src "
        "WHERE e.dst = ? AND e.kind='contains' AND owner.kind='package' "
        "ORDER BY owner.uri LIMIT 1",
        (file_id,),
    ).fetchone()
    importer_rows = conn.execute(
        f"""
        SELECT src.path, dst.name
        FROM edges e
        JOIN nodes src ON src.id = e.src
        JOIN nodes dst ON dst.id = e.dst
        WHERE e.kind='imports'
          AND (dst.id = ? OR (dst.path = ? AND dst.repo IS ?))
          AND src.repo IS ? AND src.path IS NOT NULL
          AND {_RESOLVED_FILTER}
        ORDER BY src.path, dst.name
        """,
        (file_id, path, repository, repository),
    ).fetchall()
    importer_symbols: dict[str, list[str]] = {}
    for importer_path, symbol in importer_rows:
        importer_symbols.setdefault(importer_path, []).append(symbol)

    role_flags = {
        "is_importable": bool(attrs.get("is_importable", False)),
        "has_main": bool(attrs.get("has_main", False)),
        "is_test": bool(attrs.get("is_test", False)),
        "is_config": bool(attrs.get("is_config", False)),
        "is_generated": bool(attrs.get("is_generated", False)),
        "is_type_only": bool(attrs.get("is_type_only", False)),
        "is_executable": bool(attrs.get("is_executable", False)),
    }
    package = None
    if package_row is not None and package_row[1]:
        package = (str(package_row[0]), str(package_row[1]))
    return FileDescription(
        uri=str(stored_uri),
        path=str(path),
        children=[_row_to_node(row) for row in children_rows],
        imports=[ImportRecord(name=str(row[0]), path=str(row[1])) for row in import_rows],
        imported_by=[
            ImporterRecord(path=importer_path, symbols=tuple(sorted(symbols)), depth=1)
            for importer_path, symbols in sorted(importer_symbols.items())
        ],
        package=package,
        role_flags=role_flags,
        token_count=attrs.get("token_count"),
        exports=[ExportRecord(name=row[0], kind=row[1], line=row[2]) for row in export_rows],
    )


def exported_by(conn: sqlite3.Connection, *, name: str) -> list[ExporterRecord]:
    rows = conn.execute(
        f"""
        SELECT DISTINCT src.path, dst.name
        FROM edges e
        JOIN nodes src ON e.src = src.id
        JOIN nodes dst ON e.dst = dst.id
        WHERE e.kind='exports' AND (dst.name = ? OR dst.name LIKE '%.' || ?)
          AND src.path IS NOT NULL
          AND {_RESOLVED_FILTER}
        ORDER BY src.path
        """,
        (name, name),
    ).fetchall()
    return [ExporterRecord(path=r[0], name=r[1]) for r in rows]


# ============================================================================
# Bubble-up and cross-cutting helpers.
# ============================================================================


def tests_for_package(conn: sqlite3.Connection, *, package_name: str) -> list[SuiteDescription]:
    """Return TestSuites that cover the package via `tests` edges.

    Returns [] when the package has no matching edges. Honors
    `_RESOLVED_FILTER`: suites whose `tests` edge has
    resolution='unresolved' are excluded.

    `conn` must be a `sqlite3.Connection` opened with `mode=ro`.
    """
    # _RESOLVED_FILTER uses alias `e`; substitute alias `t` for our query.
    tests_resolved_filter = _RESOLVED_FILTER.replace("e.", "t.")
    rows = conn.execute(
        f"SELECT ts.name, ts.uri, ts.attrs_json, "
        f"(SELECT COUNT(*) FROM edges pc "
        f" WHERE pc.src = ts.id AND pc.kind='physically_contains') AS fc "
        f"FROM edges t "
        f"JOIN nodes ts ON t.src = ts.id "
        f"JOIN nodes p ON t.dst = p.id "
        f"WHERE t.kind='tests' AND ts.kind='test_suite' "
        f"AND p.kind='package' AND p.name = ? "
        f"AND {tests_resolved_filter} "
        f"ORDER BY ts.name",
        (package_name,),
    ).fetchall()
    return [_load_suite_description(r) for r in rows]


def entry_points_for_package(conn: sqlite3.Connection, *, package_name: str) -> list[EntryPointDescription]:
    """Return EntryPoints declared by the package, sorted by name.

    `conn` must be a `sqlite3.Connection` opened with `mode=ro`.
    """
    rows = conn.execute(
        "SELECT ep.name, ep.uri, ep.attrs_json, f.path "
        "FROM nodes pkg "
        "JOIN edges de ON de.src = pkg.id AND de.kind='declares_entry_point' "
        "JOIN nodes ep ON ep.id = de.dst AND ep.kind='entry_point' "
        "LEFT JOIN edges ib ON ib.src = ep.id AND ib.kind='implemented_by' "
        "LEFT JOIN nodes f ON f.id = ib.dst AND f.kind='file' "
        "WHERE pkg.kind='package' AND pkg.name = ? "
        "ORDER BY ep.name",
        (package_name,),
    ).fetchall()
    return [_load_entry_point_description(r) for r in rows]


# ============================================================================
# CLI/core raw-SQL ports. Each function lifts the exact SQL from a non-wiki-io
# call site VERBATIM (same columns/WHERE/ORDER BY/JSON extraction) so callers
# can stop writing SQL with byte-for-byte identical results. Call sites noted
# per function. All take `conn` read-only and return plain data.
# ============================================================================


def metadata(conn: sqlite3.Connection, key: str) -> str | None:
    """Return the `metadata.value` for `key`, or None if absent.

    Source: graph_cli/ops_status.py `_collect` (the `last_indexed_commit`
    lookup), generalized to an arbitrary key.
    """
    row = conn.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def node_count(conn: sqlite3.Connection) -> int:
    """Total node count."""
    return int(conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0])


def node_counts_by_kind(conn: sqlite3.Connection) -> dict[str, int]:
    """`{kind: count}` over all nodes.

    Source: graph_cli/ops_status.py `_collect` (`SELECT kind, COUNT(*) FROM nodes GROUP BY kind`).
    """
    return {k: n for k, n in conn.execute("SELECT kind, COUNT(*) FROM nodes GROUP BY kind").fetchall()}


def edge_counts_by_kind(conn: sqlite3.Connection) -> dict[str, int]:
    """`{kind: count}` over all edges.

    Source: graph_cli/ops_status.py `_collect` (`SELECT kind, COUNT(*) FROM edges GROUP BY kind`).
    """
    return {k: n for k, n in conn.execute("SELECT kind, COUNT(*) FROM edges GROUP BY kind").fetchall()}


def languages(conn: sqlite3.Connection) -> list[str]:
    """Sorted distinct `attrs_json.$.language` values across all nodes.

    Source: graph_cli/ops_status.py `_collect` (SQL + `sorted(...)`) — VERBATIM,
    including the `attrs_json IS NOT NULL AND json_extract(...) IS NOT NULL`
    guard and no extra Python-side truthiness filter.
    """
    rows = conn.execute(
        "SELECT DISTINCT json_extract(attrs_json, '$.language') "
        "FROM nodes WHERE attrs_json IS NOT NULL "
        "AND json_extract(attrs_json, '$.language') IS NOT NULL"
    ).fetchall()
    return sorted(row[0] for row in rows)


def file_paths(conn: sqlite3.Connection) -> list[str]:
    """Sorted paths of all `file` nodes."""
    rows = conn.execute("SELECT path FROM nodes WHERE kind='file' AND path IS NOT NULL").fetchall()
    return sorted(r[0] for r in rows)


def file_uris(conn: sqlite3.Connection) -> list[str]:
    """Sorted repo-qualified URIs of all `file` nodes that carry one.

    `file_paths` returns bare repo-relative paths, which collide across
    repositories (`README.md`); a caller scoping by repository reads these.
    """
    rows = conn.execute("SELECT uri FROM nodes WHERE kind='file' AND uri IS NOT NULL AND uri <> ''").fetchall()
    return sorted(str(r[0]) for r in rows)


def file_paths_in_package(conn: sqlite3.Connection, name: str) -> list[str]:
    """Sorted file paths contained by the package/app named `name` (case-insensitive).

    Deduplicated through a set before sorting: a file reachable by more than
    one `contains` edge must still appear once.
    """
    rows = conn.execute(
        "SELECT f.path FROM nodes p "
        "JOIN edges ce ON ce.src = p.id AND ce.kind='contains' "
        "JOIN nodes f ON ce.dst = f.id AND f.kind='file' "
        "WHERE p.kind IN ('package','app') AND LOWER(p.name)=LOWER(?)",
        (name,),
    ).fetchall()
    return sorted({r[0] for r in rows if r[0]})


def file_attrs(conn: sqlite3.Connection, path: str) -> dict[str, Any] | None:
    """Parsed `attrs_json` for the `file` node at `path`, or None.

    Returns None when there is no row, when attrs are empty, and when the JSON
    does not parse — a malformed `attrs_json` reads as absent, never raises.
    """
    row = conn.execute("SELECT attrs_json FROM nodes WHERE kind='file' AND path=? LIMIT 1", (path,)).fetchone()
    if not row or not row[0]:
        return None
    try:
        data = json.loads(row[0])
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def files_in_node(conn: sqlite3.Connection, node_id: int) -> list[tuple[Any, ...]]:
    """`(id, path, attrs_json)` rows for `file` nodes contained by `node_id`.

    Matched via `contains` edge; filters `f.path IS NOT NULL`, ordered by `f.path`.
    """
    return conn.execute(
        "SELECT f.id, f.path, f.attrs_json "
        "FROM nodes p "
        "JOIN edges e ON e.src = p.id AND e.kind = 'contains' "
        "JOIN nodes f ON e.dst = f.id AND f.kind = 'file' "
        "WHERE p.id = ? AND f.path IS NOT NULL "
        "ORDER BY f.path",
        (node_id,),
    ).fetchall()


def symbol_names_under_files(
    conn: sqlite3.Connection,
    file_ids: Iterable[int],
    kinds: tuple[str, ...] = ("class", "function", "method"),
) -> list[str]:
    """Names of `kinds` symbols contained by any file in `file_ids`.

    Returns [] for empty `file_ids`, issuing no SQL at all.
    """
    ids = list(file_ids)
    if not ids:
        return []
    placeholders = ",".join("?" for _ in ids)
    kind_ph = ",".join("?" for _ in kinds)
    rows = conn.execute(
        f"SELECT n.name FROM edges e JOIN nodes n ON e.dst = n.id "
        f"WHERE e.src IN ({placeholders}) AND e.kind='contains' "
        f"AND n.kind IN ({kind_ph}) AND n.name IS NOT NULL",
        (*ids, *kinds),
    ).fetchall()
    return [r[0] for r in rows]


def declared_entry_points(conn: sqlite3.Connection) -> list[tuple[Any, ...]]:
    """`(package_name, entry_point_path, callable)` for every declared entry point.

    Source: graph_cli/q_list_scripts.py (the declared-annotation lookup) — SQL
    ported VERBATIM, including `json_extract(ep.attrs_json, '$.callable')` and
    the `ep.kind='entry_point' AND ep.path IS NOT NULL` filter.
    """
    return conn.execute(
        "SELECT pkg.name, ep.path, json_extract(ep.attrs_json, '$.callable') "
        "FROM nodes pkg "
        "JOIN edges de ON de.src = pkg.id AND de.kind='declares_entry_point' "
        "JOIN nodes ep ON ep.id = de.dst "
        "WHERE ep.kind='entry_point' AND ep.path IS NOT NULL"
    ).fetchall()


def node_exists(conn: sqlite3.Connection, kind: str, name: str) -> bool:
    """Whether a node of `(kind, name)` exists.

    Source: graph_cli/q_what_tests.py (the two `SELECT 1 FROM nodes WHERE
    kind=... AND name=? LIMIT 1` existence checks), generalized over `kind`.
    """
    row = conn.execute("SELECT 1 FROM nodes WHERE kind=? AND name=? LIMIT 1", (kind, name)).fetchone()
    return row is not None


# ============================================================================
# Entity-lookup + index-generation queries: name/path→entity resolution,
# consumer rollups. Callers keep application concerns (single-vs-multi
# dispatch, warnings, ordering of returned tuples).
# ============================================================================

# Entity-kind nodes worth a name-fallback match. File names are intentionally excluded.
_ENTITY_KINDS = ("package", "class", "function", "method")


def package_for_file(conn: sqlite3.Connection, path: str) -> tuple[str, str] | None:
    """Return `(name, uri)` of the `package` CONTAINING the file at `path`, or None.

    Matches via `f.kind='file'` + a `contains` edge + `p.kind='package'`, LIMIT 1.
    Tuple order is `(name, uri)` (the natural `SELECT p.name, p.uri` order) —
    callers wanting `(uri, name)` must re-order. Returns None when no row
    matches OR the matched package has a falsy uri.
    """
    row = conn.execute(
        "SELECT p.name, p.uri FROM nodes f "
        "JOIN edges e ON e.dst = f.id AND e.kind='contains' "
        "JOIN nodes p ON e.src = p.id "
        "WHERE f.kind='file' AND f.path = ? AND p.kind='package' "
        "LIMIT 1",
        (path,),
    ).fetchone()
    if row is None:
        return None
    name, uri = row
    if not uri:
        return None
    return (name, uri)


def entity_by_name(
    conn: sqlite3.Connection,
    name: str,
    kinds: tuple[str, ...] = _ENTITY_KINDS,
) -> list[tuple[Any, ...]]:
    """Return ALL entity-kind nodes named `name` (with a non-null uri) as `(name, uri, kind)`.

    Returns the full `fetchall()` list (NOT a single row) so callers handle
    single-vs-multi-match dispatch (including any filtering and re-ordering).
    Count-based branching and stderr output are caller responsibilities —
    this function only runs the query. Empty list when nothing matches.
    """
    kind_ph = ",".join("?" for _ in kinds)
    sql = f"SELECT name, uri, kind FROM nodes WHERE name = ? AND kind IN ({kind_ph}) AND uri IS NOT NULL"
    return conn.execute(sql, [name, *kinds]).fetchall()


def package_or_app_by_dir(conn: sqlite3.Connection, path: str) -> tuple[Any, ...] | None:
    """Return `(uri, name, id)` of the package/app whose `path` equals `path`, or None.

    Matches via `kind IN ('package','app')`, exact `path = ?`,
    `uri IS NOT NULL AND uri <> ''`, LIMIT 1. Caller owns ancestor-walk over
    candidate directories and type coercion; this function resolves exactly one path.
    """
    row = conn.execute(
        "SELECT uri, name, id FROM nodes "
        "WHERE kind IN ('package','app') AND path = ? AND uri IS NOT NULL AND uri <> '' LIMIT 1",
        (path,),
    ).fetchone()
    return tuple(row) if row is not None else None


def consumer_packages(
    conn: sqlite3.Connection,
    *,
    kind: str,
    entity_uri: str = "",
    entity_name: str = "",
) -> tuple[str, ...]:
    """DOMAIN-AGNOSTIC consumer/tested package (and app) names.

    Per-kind logic:
      - dependency:  `used_by` consumers, `p.kind IN ('package','app','repository')`,
                     by the dependency node's `entity_uri` (DISTINCT, ORDER BY p.name).
      - test_suite:  `tests` packages/apps by `ts.uri` (DISTINCT, ORDER BY p.name).
    Any other kind returns `()`.
    """
    if kind == "dependency":
        rows = conn.execute(
            "SELECT DISTINCT p.name FROM edges u "
            "JOIN nodes p ON u.src = p.id "
            "JOIN nodes dep ON u.dst = dep.id "
            "WHERE u.kind='used_by' AND p.kind IN ('package', 'app', 'repository') "
            "AND dep.kind='dependency' AND dep.uri = ? "
            "ORDER BY p.name",
            (entity_uri,),
        ).fetchall()
        return tuple(r[0] for r in rows)
    if kind == "test_suite":
        rows = conn.execute(
            "SELECT DISTINCT p.name FROM edges t "
            "JOIN nodes ts ON t.src = ts.id "
            "JOIN nodes p ON t.dst = p.id "
            "WHERE t.kind='tests' AND ts.kind='test_suite' AND ts.uri = ? "
            "AND p.kind IN ('package', 'app') "
            "ORDER BY p.name",
            (entity_uri,),
        ).fetchall()
        return tuple(r[0] for r in rows)
    return ()
