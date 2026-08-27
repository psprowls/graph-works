"""Manifest scanning: pyproject.toml + package.json → kind:package nodes."""

from __future__ import annotations

import json
import re
import sqlite3
import sys
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from code_graph_io import _ignore, upsert
from code_graph_io.classification import classify
from code_graph_io.records import GraphEdge, GraphNode, as_graph_records
from code_graph_io.uri import RepoContext, app_uri, pkg_uri

# PEP 508 bare-name prefix: identifier characters before any version/extra/marker.
_DEP_NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]+")


@dataclass(frozen=True, slots=True)
class ManifestDependency:
    ecosystem: Literal["pypi", "npm"]
    name: str
    spec: str
    dev: bool


@dataclass(frozen=True, slots=True)
class ManifestPackage:
    repo_root: Path
    repo: RepoContext
    package_dir: Path
    relative_path: str
    name: str
    ecosystem: Literal["pypi", "npm"]
    version: str
    description: str
    dependencies: tuple[ManifestDependency, ...]
    distributable: bool
    language: str
    app_kind: str | None
    app_signals: tuple[str, ...]


def _prune_vanished(
    conn: sqlite3.Connection,
    *,
    current_repo: str | None,
    keep_keys: set[tuple[str, str, str]],
) -> None:
    """Delete package/app nodes whose manifest facet is no longer discovered.

    `keep_keys` is (kind, name, path) — kind-aware. A member's Package facet
    is kept whenever its manifest is discovered at all; its App facet is
    kept only while classify() still returns app signals THIS pass, so a
    lost App facet is pruned even though the sibling Package's (name, path)
    is still current. See the facet_of design: there is no more in-place
    kind-flip, so a lost facet must be prunable independently of its sibling.

    Scoped to `current_repo`: a workspace member's build must only ever prune
    nodes stamped with its own repo. Unscoped, one member's `refresh()` would
    delete a sibling member's package/app nodes too. `ON DELETE CASCADE` on
    `edges.src`/`edges.dst` (schema.py) removes the node's edges along with it.
    """
    rows = conn.execute(
        "SELECT id, kind, name, path, attrs_json FROM nodes WHERE kind IN ('package', 'app') "
        "AND ((? IS NULL AND repo IS NULL) OR repo = ?)",
        (current_repo, current_repo),
    ).fetchall()
    stale_ids = [
        row[0]
        for row in rows
        # C#-sourced package rows belong to csharp_projects._prune_vanished_packages.
        # Without this, they are deleted here and re-inserted by csharp_projects.refresh
        # on every pass -- the end state is correct, but node ids churn and every edge
        # touching them is cascade-deleted and rebuilt.
        if (json.loads(row[4]) if row[4] else {}).get("language") != "csharp"
        and (row[1], row[2], row[3]) not in keep_keys
    ]
    if stale_ids:
        placeholders = ",".join("?" for _ in stale_ids)
        conn.execute(f"DELETE FROM nodes WHERE id IN ({placeholders})", stale_ids)


def _extract_dep_name(pep508_str: str) -> str | None:
    """Extract the bare package name from a PEP 508 specifier.

    Returns lowercase name, or None if the string doesn't begin with a
    valid identifier. Strips bracketed extras (`[bedrock]`), version
    specifiers, environment markers, and URL forms (`git+...#egg=...`
    is NOT supported — returns None).

    PEP 503 full normalization (`Foo.bar` -> `foo-bar`)
    is intentionally NOT applied.
    """
    s = pep508_str.strip()
    if not s or s.startswith(("git+", "http://", "https://", "-e ", ".")):
        return None
    m = _DEP_NAME_RE.match(s)
    return m.group(0).lower() if m else None


def _dependency_registry_url(ecosystem: str, name: str) -> str:
    """Return the public registry package page URL for a dependency node."""
    if ecosystem == "pypi":
        return f"https://pypi.org/project/{name}/"
    if ecosystem == "npm":
        return f"https://www.npmjs.com/package/{name}"
    if ecosystem == "nuget":
        return f"https://www.nuget.org/packages/{name}/"
    raise ValueError(f"unsupported dependency ecosystem: {ecosystem!r}")


def _should_skip(
    manifest_path: Path, repo_root: Path, skip_dirs: frozenset[str], ignore: _ignore.IgnoreSpec | None = None
) -> bool:
    """Whether the manifest at *manifest_path* is out of scope for this repo.

    Matched against the **repo-relative** path, not the absolute one: every
    other caller of `should_skip` in this package passes a repo-relative
    path, `IgnoreSpec` patterns are anchored at the repo root, and an
    absolute path would also let a `DEFAULT_SKIP_DIRS` name anywhere in the
    checkout's *parent* directories (`/Users/x/build/repo/...`) skip the
    whole tree.
    """
    rel = manifest_path.relative_to(repo_root).as_posix()
    return bool(_ignore.should_skip(rel, skip_dirs, ignore))


def _read_pyproject(path: Path) -> dict[str, Any] | None:
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError as exc:
        print(f"warning: skipping {path} ({exc})", file=sys.stderr)
        return None
    project = data.get("project") or {}
    name = project.get("name")
    if not name:
        return None
    dep_groups_raw = data.get("dependency-groups") or {}
    dep_groups: dict[str, list[str]] = {}
    if isinstance(dep_groups_raw, dict):
        for group, entries in dep_groups_raw.items():
            if isinstance(entries, list):
                dep_groups[group] = [e for e in entries if isinstance(e, str)]
    # surface [project.scripts] presence as a classify() signal.
    scripts = project.get("scripts") or {}
    # uv's own term for a `[tool.uv] package = false` manifest is a "virtual
    # project": not built or installed. Tested with `is False`, not
    # truthiness, so a string "false" or a missing key never opts a real
    # package out of the graph.
    tool = data.get("tool")
    uv_table = tool.get("uv") if isinstance(tool, dict) else None
    virtual = isinstance(uv_table, dict) and uv_table.get("package") is False
    return {
        "name": name,
        "version": project.get("version", ""),
        # description source — consumed cross-package by
        # wiki-io's scanner_frontmatter_for_node to derive the entity `summary:`.
        "description": project.get("description", ""),
        "dependencies": list(project.get("dependencies", [])),
        "dep_groups": dep_groups,  # PEP 735
        "language": "python",
        "scripts_present": bool(scripts),
        "virtual": virtual,
    }


def _read_package_json(path: Path) -> dict[str, Any] | None:
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as exc:
        print(f"warning: skipping {path} ({exc})", file=sys.stderr)
        return None
    name = data.get("name")
    if not name:
        return None
    deps = data.get("dependencies") or {}
    dev_deps = data.get("devDependencies") or {}
    # surface package.json "bin" presence as a classify() signal.
    # Truthy when bin is a non-empty string OR a dict with at least one truthy value.
    bin_val = data.get("bin")
    bin_present = bool(bin_val) and (
        (isinstance(bin_val, str) and bool(bin_val)) or (isinstance(bin_val, dict) and any(bin_val.values()))
    )
    # merge devDependencies into the classify() input list so tools like
    # electron/vite declared as dev-only are visible to signal detection.
    # The dev-origin names are preserved in a separate marker for node attrs.
    runtime_names: set[str] = set(deps.keys()) if isinstance(deps, dict) else set()
    dev_names: set[str] = set(dev_deps.keys()) if isinstance(dev_deps, dict) else set()
    merged = sorted(runtime_names | dev_names)
    # build name->spec map covering all declared deps (runtime + dev).
    # Runtime entries take precedence when a name appears in both maps.
    # Non-string spec values are coerced to "" defensively.
    dep_specs: dict[str, str] = {}
    if isinstance(dev_deps, dict):
        for dep_name, spec in dev_deps.items():
            dep_specs[dep_name] = spec if isinstance(spec, str) else ""
    if isinstance(deps, dict):
        for dep_name, spec in deps.items():
            dep_specs[dep_name] = spec if isinstance(spec, str) else ""
    return {
        "name": name,
        "version": data.get("version", ""),
        # description source (parity with pyproject).
        "description": data.get("description", ""),
        "dependencies": merged,  # runtime + dev, sorted + deduped
        "dev_dependencies": sorted(dev_names),  # dev-origin marker
        "dep_specs": dep_specs,  # name->raw-spec for all deps (runtime wins)
        "_runtime_dep_names": runtime_names,  # for is_dev computation in refresh()
        "language": "javascript",
        "bin_present": bin_present,
        # npm's "private": true is NOT the uv `package = false` equivalent —
        # it governs publishing, not whether the thing is a package.
        "virtual": False,
    }


def _discover_manifests(
    repo_root: Path, skip_dirs: frozenset[str], ignore: _ignore.IgnoreSpec | None = None
) -> list[tuple[Path, dict[str, Any]]]:
    found: list[tuple[Path, dict[str, Any]]] = []
    for manifest_path in repo_root.rglob("pyproject.toml"):
        if _should_skip(manifest_path, repo_root, skip_dirs, ignore):
            continue
        info = _read_pyproject(manifest_path)
        if info:
            found.append((manifest_path.parent, info))
    for manifest_path in repo_root.rglob("package.json"):
        if _should_skip(manifest_path, repo_root, skip_dirs, ignore):
            continue
        info = _read_package_json(manifest_path)
        if info:
            found.append((manifest_path.parent, info))
    return found


def _manifest_dependencies(info: Mapping[str, Any]) -> tuple[ManifestDependency, ...]:
    ecosystem: Literal["pypi", "npm"] = "pypi" if info["language"] == "python" else "npm"
    if ecosystem == "pypi":
        runtime = [
            ManifestDependency("pypi", name, raw[len(name) :].strip(), False)
            for raw in info["dependencies"]
            if (name := _extract_dep_name(raw)) is not None
        ]
        development = [
            ManifestDependency("pypi", name, raw[len(name) :].strip(), True)
            for entries in info["dep_groups"].values()
            for raw in entries
            if (name := _extract_dep_name(raw)) is not None
        ]
        return tuple(sorted(runtime + development, key=lambda dep: (dep.name, dep.dev, dep.spec)))
    return tuple(
        ManifestDependency("npm", name, info["dep_specs"].get(name, ""), name in info["dev_dependencies"])
        for name in sorted(info["dependencies"])
    )


def discover_manifest_packages(
    repo_root: Path,
    *,
    ctx: RepoContext,
    ignore: _ignore.IgnoreSpec | None = None,
) -> tuple[ManifestPackage, ...]:
    """Discover typed manifest inventories, ordered by relative manifest path."""
    repo_root = Path(repo_root).resolve()
    manifests: list[ManifestPackage] = []
    for package_dir, info in _discover_manifests(repo_root, _ignore.DEFAULT_SKIP_DIRS, ignore):
        package_dir = package_dir.resolve()
        relative_path = package_dir.relative_to(repo_root).as_posix()
        relative_path = "" if relative_path == "." else relative_path
        _kind, app_kind, app_signals = classify(info, package_dir)
        language = info["language"]
        ecosystem: Literal["pypi", "npm"] = "pypi" if language == "python" else "npm"
        manifests.append(
            ManifestPackage(
                repo_root=repo_root,
                repo=ctx,
                package_dir=package_dir,
                relative_path=relative_path,
                name=info["name"],
                ecosystem=ecosystem,
                version=info["version"],
                description=info.get("description", ""),
                dependencies=_manifest_dependencies(info),
                distributable=not bool(info.get("virtual")),
                language=language,
                app_kind=app_kind,
                app_signals=tuple(app_signals),
            )
        )
    return tuple(sorted(manifests, key=lambda item: item.relative_path))


def _file_nodes_under(conn: sqlite3.Connection, prefix: str, current_repo: str | None) -> list[str]:
    if current_repo is None:
        rows = conn.execute(
            "SELECT path FROM nodes WHERE kind='file' AND path LIKE ? AND attrs_json IS NOT NULL",
            (f"{prefix}%",),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT path FROM nodes WHERE kind='file' AND path LIKE ? AND attrs_json IS NOT NULL "
            "AND (repo = ? OR repo IS NULL)",
            (f"{prefix}%", current_repo),
        ).fetchall()
    return [row[0] for row in rows]


def _dominant_language(conn: sqlite3.Connection, paths: list[str], current_repo: str | None) -> str | None:
    """Return the most common file-node language among ``paths``.

    Returns None on a tie or when no path carries a language. Defensive: used
    only when a package manifest does not declare a language.
    """
    counts: dict[str, int] = {}
    for path in paths:
        if current_repo is None:
            row = conn.execute("SELECT attrs_json FROM nodes WHERE kind='file' AND path=? LIMIT 1", (path,)).fetchone()
        else:
            row = conn.execute(
                "SELECT attrs_json FROM nodes WHERE kind='file' AND path=? AND (repo = ? OR repo IS NULL) LIMIT 1",
                (path, current_repo),
            ).fetchone()
        if not row or not row[0]:
            continue
        lang = json.loads(row[0]).get("language")
        if lang:
            counts[lang] = counts.get(lang, 0) + 1
    if not counts:
        return None
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
        return None  # tie -> ambiguous -> omit
    return ranked[0][0]


def refresh(
    conn: sqlite3.Connection,
    *,
    repo_root: Path,
    ctx: RepoContext,
    manifests: Sequence[ManifestPackage],
    current_repo: str | None = None,
    ignore: _ignore.IgnoreSpec | None = None,
) -> None:
    """Upsert Package/App facets and containment from discovered manifests.

    Dependency facets are intentionally reconciled once per workspace by
    :mod:`code_graph_io.dependencies`, after every member Package exists.
    """
    repo_root = Path(repo_root).resolve()
    local_keys: set[tuple[str, str, str]] = set()
    for manifest in manifests:
        if not manifest.distributable:
            continue

        package_path = manifest.relative_path
        local_keys.add(("package", manifest.name, package_path))
        if manifest.app_kind is not None:
            local_keys.add(("app", manifest.name, package_path))

        pkg_uri_val = pkg_uri(ctx, manifest.name)
        prefix = f"{package_path}/" if package_path else ""
        contained = _file_nodes_under(conn, prefix, current_repo)
        base_attrs: dict[str, Any] = {
            "version": manifest.version,
            "description": manifest.description,
            "dependencies": [
                f"{dependency.name}{dependency.spec}" if manifest.ecosystem == "pypi" else dependency.name
                for dependency in manifest.dependencies
                if manifest.ecosystem == "npm" or not dependency.dev
            ],
            "dev_dependencies": [dependency.name for dependency in manifest.dependencies if dependency.dev],
            "language": manifest.language,
        }
        if not base_attrs["language"]:
            dominant = _dominant_language(conn, contained, current_repo)
            if dominant:
                base_attrs["language"] = dominant

        nodes = [
            GraphNode(
                kind="package",
                name=manifest.name,
                path=package_path,
                line=None,
                attrs={**base_attrs, "uri": pkg_uri_val},
            )
        ]
        edges = [
            GraphEdge(
                src=("package", manifest.name, package_path),
                dst=("file", file_path, file_path),
                kind="contains",
                attrs={},
            )
            for file_path in contained
        ]
        if manifest.app_kind is not None:
            nodes.append(
                GraphNode(
                    kind="app",
                    name=manifest.name,
                    path=package_path,
                    line=None,
                    attrs={
                        **base_attrs,
                        "uri": app_uri(ctx, manifest.name),
                        "app_kind": manifest.app_kind,
                        "app_signals": list(manifest.app_signals),
                    },
                )
            )
            edges.append(
                GraphEdge(
                    src=("package", manifest.name, package_path),
                    dst=("app", manifest.name, package_path),
                    kind="facet_of",
                    attrs={},
                )
            )
        upsert.upsert_records(conn, as_graph_records(nodes=nodes, edges=edges))

    _prune_vanished(conn, current_repo=current_repo, keep_keys=local_keys)
