"""MSBuild solution/project scanning: .sln + .csproj -> kind:solution/package nodes."""

from __future__ import annotations

import json
import re
import sqlite3
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from code_graph_io import _ignore, upsert
from code_graph_io.packages import _dependency_registry_url
from code_graph_io.records import GraphEdge, GraphNode, as_graph_records
from code_graph_io.uri import RepoContext, dependency_path, dependency_uri, pkg_uri, solution_uri

# Classic .sln "Project(...) = "Name", "relative\path.csproj", "{GUID}"" line.
_SLN_PROJECT_RE = re.compile(
    r'^Project\("\{[0-9A-Fa-f-]+\}"\)\s*=\s*"([^"]+)",\s*"([^"]+)",\s*"\{[0-9A-Fa-f-]+\}"',
    re.MULTILINE,
)

# MSBuild item element whose local (namespace-stripped) tag name marks a
# NuGet package reference, e.g. <PackageReference Include="X" Version="Y" />.
_PACKAGE_REF_TAG_SUFFIXES = ("PackageReference",)


@dataclass(frozen=True)
class DiscoveredSolution:
    """One `.sln` this pass discovered, for Repository-containment linking
    deferred to `link_repository_solutions`, once the Repository node exists
    (created by `structural_nodes.emit`).
    """

    name: str
    path: str  # repo-relative path to the .sln file's containing directory


def _parse_sln(sln_path: Path) -> list[tuple[str, str]]:
    """Return [(project_name, relative_csproj_path), ...] from a classic .sln."""
    text = sln_path.read_text(encoding="utf-8", errors="replace")
    out: list[tuple[str, str]] = []
    for m in _SLN_PROJECT_RE.finditer(text):
        name, rel = m.group(1), m.group(2)
        if not rel.lower().endswith(".csproj"):
            continue
        out.append((name, rel.replace("\\", "/")))
    return out


def _safe_rel(path: Path, repo_root: Path) -> str | None:
    """Repo-relative POSIX path, or None when *path* escapes *repo_root*.

    A `.sln` may reference a `.csproj` outside the checkout (`..\\Shared\\...`).
    `Path.relative_to` raises ValueError there; returning None lets the caller
    skip and record the entry instead of aborting the whole update pass.
    """
    try:
        rel = path.relative_to(repo_root).as_posix()
    except ValueError:
        return None
    return "" if rel == "." else rel


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _parse_csproj(csproj_path: Path) -> dict[str, Any] | None:
    try:
        tree = ET.parse(csproj_path)
    except ET.ParseError as exc:
        print(f"warning: skipping {csproj_path} ({exc})", file=sys.stderr)
        return None
    root = tree.getroot()
    package_refs: dict[str, str] = {}
    for elem in root.iter():
        if _local_name(elem.tag) in _PACKAGE_REF_TAG_SUFFIXES:
            name = elem.get("Include")
            version = elem.get("Version")
            if version is None:
                version = next(
                    ((child.text or "").strip() for child in elem if _local_name(child.tag) == "Version"),
                    "",
                )
            if name:
                package_refs[name] = version
    return {"name": csproj_path.stem, "package_refs": package_refs}


def _should_skip(
    file_path: Path, repo_root: Path, skip_dirs: frozenset[str], ignore: _ignore.IgnoreSpec | None = None
) -> bool:
    """Whether the `.sln`/`.csproj` at *file_path* is out of scope for this repo.

    Matched against the **repo-relative** path — mirrors
    `packages._should_skip`, which explains why: `IgnoreSpec` patterns are
    anchored at the repo root, and an absolute path would let a
    `DEFAULT_SKIP_DIRS` name anywhere in the checkout's parent directories
    skip the whole tree.
    """
    rel = file_path.relative_to(repo_root).as_posix()
    return bool(_ignore.should_skip(rel, skip_dirs, ignore))


def _discover(
    repo_root: Path, skip_dirs: frozenset[str], ignore: _ignore.IgnoreSpec | None = None
) -> tuple[list[tuple[Path, list[tuple[str, str]]]], list[Path]]:
    slns = [
        (sln_path, _parse_sln(sln_path))
        for sln_path in sorted(repo_root.rglob("*.sln"))
        if not _should_skip(sln_path, repo_root, skip_dirs, ignore)
    ]
    csprojs = [
        csproj_path
        for csproj_path in sorted(repo_root.rglob("*.csproj"))
        if not _should_skip(csproj_path, repo_root, skip_dirs, ignore)
    ]
    return slns, csprojs


def refresh(
    conn: sqlite3.Connection,
    *,
    repo_root: Path,
    ctx: RepoContext,
    current_repo: str | None = None,
    ignore: _ignore.IgnoreSpec | None = None,
) -> list[DiscoveredSolution]:
    """Rescan .sln/.csproj under `repo_root`; upsert solution/package/dependency
    nodes and groups_project/used_by edges. The dependency nodes are this
    repository's own (repository-scoped URIs). Prunes vanished solutions/projects.

    `ignore` is this member's compiled `ignore:` patterns, applied on top of
    the unconditional `_ignore.DEFAULT_SKIP_DIRS` floor — mirrors
    `packages.refresh`'s own `ignore=` handling.
    """
    repo_root = Path(repo_root).resolve()
    skip_dirs = _ignore.DEFAULT_SKIP_DIRS
    slns, all_csprojs = _discover(repo_root, skip_dirs, ignore)

    solution_nodes: list[GraphNode] = []
    solution_edges: list[GraphEdge] = []
    discovered: list[DiscoveredSolution] = []
    keep_solution_keys: set[tuple[str, str]] = set()
    solution_project_paths: list[tuple[str, str, Path]] = []

    for sln_path, entries in slns:
        sln_rel = _safe_rel(sln_path.parent.resolve(), repo_root)
        assert sln_rel is not None  # rglob discovers only paths under repo_root
        sol_name = sln_path.stem
        sol_uri = solution_uri(ctx, sol_name)
        keep_solution_keys.add((sol_name, sln_rel))
        discovered.append(DiscoveredSolution(name=sol_name, path=sln_rel))
        external: list[str] = []
        for _proj_name, rel_csproj in entries:
            csproj_abs = (sln_path.parent / rel_csproj).resolve()
            if not csproj_abs.is_file():
                continue
            proj_dir_rel = _safe_rel(csproj_abs.parent.resolve(), repo_root)
            if proj_dir_rel is None:
                external.append(rel_csproj)
                continue
            solution_project_paths.append((sol_name, sln_rel, csproj_abs))
        sol_attrs: dict[str, Any] = {"uri": sol_uri}
        if external:
            sol_attrs["external_projects"] = sorted(external)
        solution_nodes.append(GraphNode(kind="solution", name=sol_name, path=sln_rel, line=None, attrs=sol_attrs))

    keep_package_keys: set[tuple[str, str]] = set()
    package_nodes: list[GraphNode] = []
    package_edges: list[GraphEdge] = []
    # Accumulator for dependency ingestion: (ecosystem, name) -> {versions_in_use}
    dep_acc: dict[tuple[str, str], dict[str, list[str]]] = {}

    for csproj_path in all_csprojs:
        info = _parse_csproj(csproj_path)
        if info is None:
            continue
        proj_dir_rel = csproj_path.parent.resolve().relative_to(repo_root).as_posix()
        proj_dir_rel = "" if proj_dir_rel == "." else proj_dir_rel
        name = str(info["name"])
        keep_package_keys.add((name, proj_dir_rel))
        package_nodes.append(
            GraphNode(
                kind="package",
                name=name,
                path=proj_dir_rel,
                line=None,
                attrs={"uri": pkg_uri(ctx, name), "language": "csharp"},
            )
        )
        package_refs: dict[str, str] = info["package_refs"]
        for dep_name, version in package_refs.items():
            dep_key = ("nuget", dep_name)
            dep_path = dependency_path(ctx, "nuget", dep_name)
            bucket = dep_acc.setdefault(dep_key, {"versions_in_use": []})
            if version and version not in bucket["versions_in_use"]:
                bucket["versions_in_use"].append(version)
            package_edges.append(
                GraphEdge(
                    src=("package", name, proj_dir_rel),
                    dst=("dependency", dep_name, dep_path),
                    kind="used_by",
                    attrs={},
                )
            )

    # A solution may list ignored or malformed projects.  Emit a containment
    # edge only when the referenced project produced a real package node above;
    # otherwise upsert would materialize a bare package placeholder.
    for sol_name, sln_rel, csproj_path in solution_project_paths:
        proj_dir_rel = csproj_path.parent.resolve().relative_to(repo_root).as_posix()
        proj_dir_rel = "" if proj_dir_rel == "." else proj_dir_rel
        package_key = (csproj_path.stem, proj_dir_rel)
        if package_key not in keep_package_keys:
            continue
        solution_edges.append(
            GraphEdge(
                src=("solution", sol_name, sln_rel),
                dst=("package", *package_key),
                kind="groups_project",
                attrs={},
            )
        )

    dep_nodes: list[GraphNode] = []
    for (ecosystem, dep_name), bucket in sorted(dep_acc.items()):
        dep_path = dependency_path(ctx, ecosystem, dep_name)
        dep_nodes.append(
            GraphNode(
                kind="dependency",
                name=dep_name,
                path=dep_path,
                line=None,
                attrs={
                    "uri": dependency_uri(ctx, ecosystem, dep_name),
                    "ecosystem": ecosystem,
                    "name": dep_name,
                    "url": _dependency_registry_url(ecosystem, dep_name),
                    "versions_in_use": sorted(set(bucket["versions_in_use"])),
                },
            )
        )

    all_nodes = solution_nodes + package_nodes + dep_nodes
    all_edges = solution_edges + package_edges
    if all_nodes or all_edges:
        upsert.upsert_records(conn, as_graph_records(nodes=all_nodes, edges=all_edges))

    _prune_vanished_solutions(conn, current_repo=current_repo, keep_keys=keep_solution_keys)
    _prune_vanished_packages(conn, current_repo=current_repo, keep_keys=keep_package_keys)

    return discovered


def _prune_vanished_solutions(
    conn: sqlite3.Connection, *, current_repo: str | None, keep_keys: set[tuple[str, str]]
) -> None:
    rows = conn.execute(
        "SELECT id, name, path FROM nodes WHERE kind = 'solution' AND ((? IS NULL AND repo IS NULL) OR repo = ?)",
        (current_repo, current_repo),
    ).fetchall()
    stale_ids = [row[0] for row in rows if (row[1], row[2]) not in keep_keys]
    if stale_ids:
        placeholders = ",".join("?" for _ in stale_ids)
        conn.execute(f"DELETE FROM nodes WHERE id IN ({placeholders})", stale_ids)


def _prune_vanished_packages(
    conn: sqlite3.Connection, *, current_repo: str | None, keep_keys: set[tuple[str, str]]
) -> None:
    """Prune C#-sourced package nodes no longer discovered.

    Scoped to `attrs.language == "csharp"` so this never touches a
    Python/JS package node that happens to share the same (name, path) key
    space (`packages.py`'s own `_prune_vanished` owns those).
    """
    rows = conn.execute(
        "SELECT id, name, path, attrs_json FROM nodes WHERE kind = 'package' "
        "AND ((? IS NULL AND repo IS NULL) OR repo = ?)",
        (current_repo, current_repo),
    ).fetchall()

    stale_ids = []
    for row_id, name, path, attrs_json in rows:
        attrs = json.loads(attrs_json) if attrs_json else {}
        if attrs.get("language") != "csharp":
            continue
        if (name, path) not in keep_keys:
            stale_ids.append(row_id)
    if stale_ids:
        placeholders = ",".join("?" for _ in stale_ids)
        conn.execute(f"DELETE FROM nodes WHERE id IN ({placeholders})", stale_ids)


def link_repository_solutions(
    conn: sqlite3.Connection, discovered: list[DiscoveredSolution], *, ctx: RepoContext
) -> None:
    """Emit Repository -> Solution physically_contains edges.

    Deferred from `refresh()` because the Repository node doesn't exist until
    `structural_nodes.emit` runs. Call this after `structural_nodes.emit`.
    """
    if not discovered:
        return
    edges = [
        GraphEdge(
            src=("repository", ctx.repo, ""),
            dst=("solution", sol.name, sol.path),
            kind="physically_contains",
            attrs={},
        )
        for sol in discovered
    ]
    upsert.upsert_records(conn, as_graph_records(nodes=[], edges=edges))
