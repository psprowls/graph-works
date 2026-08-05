"""Entry-point emitter: EntryPoint + declares_entry_point + implemented_by.

Reads declared entry points from pyproject.toml ([project.scripts],
[project.entry-points.<group>]) and package.json (bin, main, module,
exports) for every Package row written by packages.refresh. Emits
EntryPoint nodes with strict path-qualified implemented_by resolution;
on miss, emits the EntryPoint with implemented_by=NULL plus a stderr warning.

Conventional executable files (shebang scripts) do NOT produce EntryPoint
nodes — they ride on File.is_executable.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import tomllib
from pathlib import Path

from code_parser.projections.graph import GraphEdge, GraphNode

from code_graph_io import upsert
from code_graph_io.records import as_graph_records
from code_graph_io.structural_nodes import _resolve_import_root
from code_graph_io.uri import RepoContext, entry_point_uri, repo_uri

# --- Module-private constants ---

# Conditional-export keys per the Node.js exports spec — any key inside
# `exports` matching this set is a condition selector, not a sub-path.
_EXPORT_CONDITION_KEYS: frozenset[str] = frozenset(
    {
        "import",
        "require",
        "default",
        "node",
        "browser",
        "types",
        "deno",
        "worker",
    }
)


# --- Helpers (stubs at this task; filled in subsequent tasks) ---


def _emit_pyproject_entries(
    pkg_name: str,
    pkg_rel: str,
    pkg_dir: Path,
    ctx: RepoContext,
    repo_root: Path,
    pkg_kind: str = "package",
) -> tuple[list[GraphNode], list[GraphEdge]]:
    """Parse pyproject.toml and emit EntryPoint nodes + edges.

    Handles [project.scripts] (executable, source='pyproject.scripts') and
    [project.entry-points.<group>] (executable for console_scripts, library
    otherwise; source='pyproject.entry-points.<group>'). Resolves
    implemented_by strictly: the dotted module prefix must start with the
    Package's importable name; on miss emits the EntryPoint with no
    implemented_by edge plus a stderr warning.
    """
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []

    pyproject = pkg_dir / "pyproject.toml"
    if not pyproject.exists():
        return [], []
    try:
        entry_source_path = pyproject.resolve().relative_to(repo_root).as_posix()
    except ValueError as exc:
        raise ValueError("graph node path is required for this projection") from exc

    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        print(
            f"[entry_points] warning: failed to parse {pyproject}: {exc}",
            file=sys.stderr,
        )
        return [], []

    project = data.get("project", {}) or {}
    importable = pkg_name.replace("-", "_")
    import_root = _resolve_import_root(pkg_dir, importable)

    # pkg_key uses the caller-supplied kind so apps emit
    # src=("app", ...) for their declares_entry_point edges.
    # Root packages carry rel-path '' (packages.refresh canonical form, never
    # None); pass it through verbatim so the edge resolves to the EXISTING
    # package/app node instead of stubbing an empty-uri duplicate.
    pkg_key = (pkg_kind, pkg_name, pkg_rel)

    def _resolve_callable(value: str) -> tuple[str | None, str | None]:
        """Return (file_rel_to_repo, callable_name) or (None, callable_or_None)."""
        if ":" not in value:
            return None, None
        module_part, func_name = value.split(":", 1)
        if import_root is None:
            return None, func_name
        parts = module_part.split(".")
        if not parts or parts[0] != import_root.name:
            return None, func_name
        rest = parts[1:]
        if not rest:
            candidate = import_root / "__init__.py"
        else:
            py_path = import_root.joinpath(*rest[:-1], rest[-1] + ".py")
            init_path = import_root.joinpath(*rest, "__init__.py")
            if py_path.exists():
                candidate = py_path
            elif init_path.exists():
                candidate = init_path
            else:
                return None, func_name
        if not candidate.exists():
            return None, func_name
        try:
            file_rel = candidate.resolve().relative_to(repo_root).as_posix()
        except ValueError:
            return None, func_name
        return file_rel, func_name

    def _emit_entry(ep_name: str, value: str, *, kind: str, source: str) -> None:
        file_rel, func_name = _resolve_callable(value)
        attrs = {
            "uri": entry_point_uri(ctx, pkg_name, ep_name),
            "entry_kind": kind,
            "source": source,
            "callable": func_name,
            "is_wildcard": False,
        }
        nodes.append(
            GraphNode(
                kind="entry_point",
                name=ep_name,
                path=entry_source_path,
                line=None,
                attrs=attrs,
            )
        )
        ep_key = ("entry_point", ep_name, entry_source_path)
        edges.append(
            GraphEdge(
                src=pkg_key,
                dst=ep_key,
                kind="declares_entry_point",
                attrs={},
            )
        )
        if file_rel is not None:
            edges.append(
                GraphEdge(
                    src=ep_key,
                    dst=("file", file_rel, file_rel),
                    kind="implemented_by",
                    attrs={},
                )
            )
        else:
            print(
                f"[entry_points] warning: cannot resolve implemented_by "
                f"for {pkg_name} entry '{ep_name}' = '{value}' "
                f"(manifest: {pyproject})",
                file=sys.stderr,
            )

    # [project.scripts]
    scripts = project.get("scripts", {}) or {}
    if isinstance(scripts, dict):
        for ep_name, value in scripts.items():
            if isinstance(value, str):
                _emit_entry(ep_name, value, kind="executable", source="pyproject.scripts")

    # [project.entry-points.<group>]
    entry_points_table = project.get("entry-points", {}) or {}
    if isinstance(entry_points_table, dict):
        for group, group_entries in entry_points_table.items():
            if not isinstance(group_entries, dict):
                continue
            ep_kind = "executable" if group == "console_scripts" else "library"
            ep_source = f"pyproject.entry-points.{group}"
            for ep_name, value in group_entries.items():
                if isinstance(value, str):
                    _emit_entry(ep_name, value, kind=ep_kind, source=ep_source)

    return nodes, edges


def _walk_exports(
    obj: object,
    *,
    key_path: str,
    condition: str | None,
    callback,
    source: str,
) -> None:
    """Recursively walk a package.json exports object, calling callback for
    each string-valued leaf with a derived key path.

    - Strings are leaves; the callback receives the derived name (key path),
      the path value, the current condition selector, and a wildcard flag.
    - Dicts split into condition selectors (keys in _EXPORT_CONDITION_KEYS)
      and sub-path keys (starting with "./" or just "."). Condition keys
      recurse with an updated condition; sub-path keys extend the key path.
    - Anything else is skipped.
    """
    if isinstance(obj, str):
        is_wildcard = "*" in obj
        callback(
            key_path,
            None if is_wildcard else obj,
            entry_kind="library",
            source=source,
            condition=condition,
            is_wildcard=is_wildcard,
            path_pattern=obj if is_wildcard else None,
        )
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in _EXPORT_CONDITION_KEYS:
                _walk_exports(
                    v,
                    key_path=key_path,
                    condition=k,
                    callback=callback,
                    source=source,
                )
            elif k == "." or k.startswith("./"):
                new_key = k if key_path == "." else f"{key_path}/{k.removeprefix('./')}"
                _walk_exports(
                    v,
                    key_path=new_key,
                    condition=condition,
                    callback=callback,
                    source=source,
                )


def _emit_packagejson_entries(
    pkg_name: str,
    pkg_rel: str,
    pkg_dir: Path,
    ctx: RepoContext,
    repo_root: Path,
    pkg_kind: str = "package",
) -> tuple[list[GraphNode], list[GraphEdge]]:
    """Parse package.json and emit EntryPoint nodes + edges.

    Handles 'main' / 'module' (library), 'bin' (executable, string or object
    form ), and a recursive walk of 'exports' (library).
    Path resolution: leading './' stripped, resolved against the Package
    directory; missing files yield a stderr warning + no implemented_by edge.
    Wildcards (`*`) are never resolved to files (path expansion deferred).
    """
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []
    pjson = pkg_dir / "package.json"
    if not pjson.exists():
        return [], []
    try:
        package_json_source_path = pjson.resolve().relative_to(repo_root).as_posix()
    except ValueError as exc:
        raise ValueError("graph node path is required for this projection") from exc

    try:
        data = json.loads(pjson.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(
            f"[entry_points] warning: failed to parse {pjson}: {exc}",
            file=sys.stderr,
        )
        return [], []

    # pkg_key uses the caller-supplied kind so apps emit
    # src=("app", ...) for their declares_entry_point edges.
    # Root packages carry rel-path '' (packages.refresh canonical form, never
    # None); pass it through verbatim so the edge resolves to the EXISTING
    # package/app node instead of stubbing an empty-uri duplicate.
    pkg_key = (pkg_kind, pkg_name, pkg_rel)
    pkgjson_name = data.get("name", pkg_name) if isinstance(data, dict) else pkg_name

    def _resolve_path(value: str) -> str | None:
        if "*" in value:
            return None
        cleaned = value
        if cleaned.startswith("./"):
            cleaned = cleaned[2:]
        candidate = (pkg_dir / cleaned).resolve()
        if not candidate.exists():
            return None
        try:
            return candidate.relative_to(repo_root).as_posix()
        except ValueError:
            return None

    def _emit_entry(
        ep_name: str,
        value: str | None,
        *,
        entry_kind: str,
        source: str,
        condition: str | None = None,
        is_wildcard: bool = False,
        path_pattern: str | None = None,
    ) -> None:
        # Disambiguate conditional exports via the upsert path slot — two
        # EntryPoint nodes with the same export key ("." for example) and
        # different conditions ("import" vs "require") must be distinct
        # rows. The condition is also stored in attrs for queries.
        node_path = f"{package_json_source_path}#condition:{condition}" if condition else package_json_source_path
        attrs = {
            "uri": entry_point_uri(ctx, pkg_name, ep_name),
            "entry_kind": entry_kind,
            "source": source,
            "callable": None,
            "condition": condition,
            "is_wildcard": is_wildcard,
            "path_pattern": path_pattern,
        }
        nodes.append(
            GraphNode(
                kind="entry_point",
                name=ep_name,
                path=node_path,
                line=None,
                attrs=attrs,
            )
        )
        ep_key = ("entry_point", ep_name, node_path)
        edges.append(
            GraphEdge(
                src=pkg_key,
                dst=ep_key,
                kind="declares_entry_point",
                attrs={},
            )
        )
        if value is not None and not is_wildcard:
            file_rel = _resolve_path(value)
            if file_rel is not None:
                edges.append(
                    GraphEdge(
                        src=ep_key,
                        dst=("file", file_rel, file_rel),
                        kind="implemented_by",
                        attrs={},
                    )
                )
            else:
                print(
                    f"[entry_points] warning: cannot resolve implemented_by "
                    f"for {pkg_name} entry '{ep_name}' = '{value}' "
                    f"(manifest: {pjson})",
                    file=sys.stderr,
                )

    if not isinstance(data, dict):
        return [], []

    # main, module
    main_val = data.get("main")
    if isinstance(main_val, str):
        _emit_entry("main", main_val, entry_kind="library", source="package.json.main")
    module_val = data.get("module")
    if isinstance(module_val, str):
        _emit_entry("module", module_val, entry_kind="library", source="package.json.module")

    # bin (string or object)
    bin_val = data.get("bin")
    if isinstance(bin_val, str):
        _emit_entry(
            pkgjson_name,
            bin_val,
            entry_kind="executable",
            source="package.json.bin",
        )
    elif isinstance(bin_val, dict):
        for bin_key, bin_path in bin_val.items():
            if isinstance(bin_path, str):
                _emit_entry(
                    bin_key,
                    bin_path,
                    entry_kind="executable",
                    source="package.json.bin",
                )

    # exports (recursive)
    exports = data.get("exports")
    if exports is not None:
        _walk_exports(
            exports,
            key_path=".",
            condition=None,
            callback=_emit_entry,
            source="package.json.exports",
        )

    return nodes, edges


# shebang scripts are NOT entry points — they ride on
# File.is_executable: Do NOT add shebang-script handling here.
# _SHEBANG_NOT_ENTRY_POINT = True


# --- Public emit() ---


def emit(
    conn: sqlite3.Connection,
    *,
    repo_root: Path,
    ctx: RepoContext,
    skip_dirs: frozenset[str],
) -> None:
    """Emit EntryPoint nodes, declares_entry_point edges, implemented_by edges
    for every declared entry across every Package's manifest."""
    repo_root = Path(repo_root).resolve()

    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []

    # include both Package and App nodes; apps still declare
    # entry points via the same pyproject.toml / package.json manifest fields.
    # Multi-repo: scope to this member (see structural_nodes.emit).
    member_repo = repo_uri(ctx)
    pkg_rows = conn.execute(
        "SELECT name, path, attrs_json, kind FROM nodes WHERE kind IN ('package', 'app') "
        "AND (repo = ? OR repo IS NULL)",
        (member_repo,),
    ).fetchall()

    for pkg_name, pkg_rel, pkg_attrs_json, pkg_kind in pkg_rows:
        pkg_attrs = json.loads(pkg_attrs_json) if pkg_attrs_json else {}
        language = pkg_attrs.get("language")
        pkg_dir = (repo_root / pkg_rel).resolve() if pkg_rel else repo_root

        if language == "python":
            pp_nodes, pp_edges = _emit_pyproject_entries(
                pkg_name, pkg_rel or "", pkg_dir, ctx, repo_root, pkg_kind=pkg_kind
            )
            nodes.extend(pp_nodes)
            edges.extend(pp_edges)
        elif language in {"javascript", "typescript"}:
            pj_nodes, pj_edges = _emit_packagejson_entries(
                pkg_name, pkg_rel or "", pkg_dir, ctx, repo_root, pkg_kind=pkg_kind
            )
            nodes.extend(pj_nodes)
            edges.extend(pj_edges)
        # Unknown languages: skip silently (no EntryPoint emission).

    upsert.upsert_records(conn, as_graph_records(nodes=nodes, edges=edges))
