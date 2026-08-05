"""Manifest scanning: pyproject.toml + package.json → kind:package nodes."""

from __future__ import annotations

import json
import re
import sqlite3
import sys
import tomllib
from pathlib import Path
from typing import Any

from code_parser.projections.graph import GraphEdge, GraphNode

from code_graph_io import _ignore, upsert
from code_graph_io.classification import classify
from code_graph_io.records import as_graph_records
from code_graph_io.uri import RepoContext, app_uri, dependency_uri, pkg_uri, repo_uri

# PEP 508 bare-name prefix: identifier characters before any version/extra/marker.
_DEP_NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]+")

# edge kind for an internal workspace package→package dependency.
# Free-text in edges.kind (schema.py), so no migration — a Domain→Domain
# "depends_on" and this Package→Package "depends_on_package" are distinct rows.
_DEPENDS_ON_PACKAGE_KIND = "depends_on_package"


def _normalize_name(name: str) -> str:
    """Canonicalize a package/dependency name for cross-form comparison.

    lowercase and collapse ``-`` to ``_`` so a declared
    dependency string (``code-graph-io``) matches a workspace package name
    (``code_graph_io`` / ``code-graph-io``) regardless of separator or case. Mirrors the
    ``.replace("-", "_")`` normalization already used in
    ``import_scan._build_importable_maps``.
    """
    return name.lower().replace("-", "_")


def _owning_repo(
    global_ws: dict[str, tuple[str, str, str, str]],
    dep_norm: str,
    current_repo: str | None,
) -> str | None:
    """Repo URI that owns the workspace package `dep_norm`.

    Falls back to `current_repo` when the package isn't in the cross-member
    index (single-repo / local-only), so callers treat it as same-repo.
    """
    entry = global_ws.get(dep_norm)
    return entry[3] if entry else current_repo


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
    raise ValueError(f"unsupported dependency ecosystem: {ecosystem!r}")


def _should_skip(manifest_path: Path, repo_root: Path, skip_dirs: frozenset[str]) -> bool:
    if _ignore.should_skip(str(manifest_path), skip_dirs):
        return True
    return False


def _is_plugin_root(manifest_dir: Path) -> bool:
    """True when `manifest_dir` is a claude-code plugin root (has
    `.claude-plugin/plugin.json`). Such a manifest is owned by the
    agent_plugin detector, not the package emitter."""
    return (manifest_dir / ".claude-plugin" / "plugin.json").exists()


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
    }


def _read_package_json(path: Path) -> dict[str, Any] | None:
    try:
        with path.open() as f:
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
    }


def _discover_manifests(repo_root: Path, skip_dirs: frozenset[str]) -> list[tuple[Path, dict[str, Any]]]:
    found: list[tuple[Path, dict[str, Any]]] = []
    for manifest_path in repo_root.rglob("pyproject.toml"):
        if _should_skip(manifest_path, repo_root, skip_dirs):
            continue
        if _is_plugin_root(manifest_path.parent):
            continue
        info = _read_pyproject(manifest_path)
        if info:
            found.append((manifest_path.parent, info))
    for manifest_path in repo_root.rglob("package.json"):
        if _should_skip(manifest_path, repo_root, skip_dirs):
            continue
        if _is_plugin_root(manifest_path.parent):
            continue
        info = _read_package_json(manifest_path)
        if info:
            found.append((manifest_path.parent, info))
    return found


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


def build_workspace_index(members: list[Path]) -> dict[str, tuple[str, str, str, str]]:
    """Union package index across member repos for cross-repo dep resolution.

    Maps normalized package name -> (stored_kind, real_name, rel_path, repo_uri),
    rel_path relative to the package's OWN member repo root.
    """
    from code_graph_io.repo_context import repo_context  # noqa: PLC0415 — avoid import cycle at module load

    index: dict[str, tuple[str, str, str, str]] = {}
    for member in members:
        member = Path(member).resolve()
        ctx = repo_context(member)
        ruri = repo_uri(ctx)
        skip_dirs = _ignore.load_skip_dirs(member)
        for pkg_dir, info in _discover_manifests(member, skip_dirs):
            rel = pkg_dir.resolve().relative_to(member).as_posix()
            rel = "" if rel == "." else rel
            ws_kind, _app_kind, _sig = classify(info, pkg_dir)
            index[_normalize_name(info["name"])] = (ws_kind, info["name"], rel, ruri)
    return index


def refresh(
    conn: sqlite3.Connection,
    *,
    repo_root: Path,
    ctx: RepoContext,
    current_repo: str | None = None,
    global_workspace: dict[str, tuple[str, str, str, str]] | None = None,
    deferred_cross_repo: list | None = None,
) -> None:
    """Rescan manifests under `repo_root` and upsert kind:package nodes + contains edges.

    `ctx` carries the (org, repo) identifiers used to compose the
    `pkg:org/repo/name` URI written onto every Package node.

    Containment is by directory-prefix: every file under a package's directory
    subtree gets a `contains` edge from that package. A manifest at the repo
    root therefore "owns" every file in the graph; sub-package manifests
    create additional `contains` edges, so a file inside a sub-package will
    have edges from BOTH the sub-package and the root package. Query callers
    that want a single owner should pick longest-prefix-wins.
    """
    repo_root = Path(repo_root).resolve()
    skip_dirs = _ignore.load_skip_dirs(repo_root)
    manifests = _discover_manifests(repo_root, skip_dirs)

    # build the workspace-package-name set + a normalized-name ->
    # (stored_kind, rel_path) map ONCE, before any dep accumulation, from the
    # already-materialized manifest list. The stored kind is the classify()-derived
    # package/app kind so the retargeted used_by / new depends_on_package edges
    # resolve to the real node (mirroring derived_edges.py:148-153).
    workspace_names: set[str] = set()
    workspace_kinds: dict[str, tuple[str, str, str]] = {}
    for pkg_dir, info in manifests:
        rel = pkg_dir.resolve().relative_to(repo_root).as_posix()
        rel = "" if rel == "." else rel
        ws_kind, _app_kind, _app_signals = classify(info, pkg_dir)
        norm = _normalize_name(info["name"])
        workspace_names.add(norm)
        # Store the workspace package's ACTUAL node name (info["name"]) — the
        # consumer may declare it under a different separator/case spelling, but
        # the edge dst must match the real node so it resolves instead of
        # inserting a stub.
        workspace_kinds[norm] = (ws_kind, info["name"], rel)

    # Multi-repo: merge the cross-member package index so a dependency
    # naming a sibling-repo package is recognized as internal (not emitted as an
    # external `dependency` node). Local entries win on name collision — the
    # branch below uses global_ws[...][3] only to decide same-repo vs cross-repo.
    global_ws = global_workspace or {}
    for norm, (g_kind, g_name, g_rel, _g_repo) in global_ws.items():
        workspace_names.add(norm)
        workspace_kinds.setdefault(norm, (g_kind, g_name, g_rel))

    # Accumulator for dependency ingestion: (ecosystem, name) -> {versions_in_use}
    dep_acc: dict[tuple[str, str], dict[str, list[str]]] = {}
    # track consumer kind so used_by edges from App nodes use src=("app", ...).
    # extended with is_dev bool so dev-origin JS edges carry attrs={"dev": True}.
    used_by_pairs: list[tuple[str, str, str, str, str, bool]] = []
    # internal package→package relationships, carrying
    # both endpoints' resolved (kind, name, rel_path) so the retargeted used_by
    # and the new depends_on_package edge point at the real package/app nodes.
    internal_pkg_edges: list[
        tuple[str, str, str, str, str, str]
    ] = []  # (consumer_name, consumer_rel, consumer_kind, target_name, target_rel, target_kind)
    for pkg_dir, info in manifests:
        rel_prefix = pkg_dir.resolve().relative_to(repo_root).as_posix()
        if rel_prefix == ".":
            rel_prefix = ""

        # derive kind, URI, and attrs in one inline pass.
        new_kind, app_kind, app_signals = classify(info, pkg_dir)
        new_uri = app_uri(ctx, info["name"]) if new_kind == "app" else pkg_uri(ctx, info["name"])

        # Hoist contained-file list so it's available for the defensive language
        # fallback below AND for the contains-edge loop that follows attrs.
        prefix = f"{rel_prefix}/" if rel_prefix else ""
        contained = _file_nodes_under(conn, prefix, current_repo)

        attrs: dict[str, Any] = {
            "version": info["version"],
            # source — stored in attrs_json so wiki-io can
            # read node.attrs["description"] uniformly across kinds.
            # Empty when pyproject has no [project].description; the TODO fallback
            # is wiki-io's job, not synthesized here.
            "description": info.get("description", ""),
            "dependencies": info["dependencies"],
            # dev-origin marker for JS packages (empty list for Python
            # manifests which have no devDependencies field).
            "dev_dependencies": info.get("dev_dependencies", []),
            "language": info["language"],
            "uri": new_uri,
        }
        # Defensive fallback: if the manifest reader did not declare a language
        # (future manifest types), infer it from the dominant language of the
        # contained file nodes. Normal builds never reach this branch.
        if not attrs.get("language"):
            dom = _dominant_language(conn, contained, current_repo)
            if dom:
                attrs["language"] = dom
        if new_kind == "app":
            # invariant: only App nodes carry app_kind / app_signals.
            attrs["app_kind"] = app_kind
            attrs["app_signals"] = app_signals

        # probe the opposite-kind row from a prior run and flip
        # it in place so the row id is preserved (every inbound edge FK stays
        # valid). The outer store.transaction() boundary set by update.run()
        # gives this UPDATE read-your-own-writes semantics for the subsequent
        # upsert_records call.
        other_kind = "package" if new_kind == "app" else "app"
        package_path = rel_prefix
        other_id = upsert._node_id(conn, (other_kind, info["name"], package_path))
        if other_id is not None:
            # Mirror _upsert_node's convention: the "uri" key lives in the
            # nodes.uri column, not attrs_json.
            attrs_for_db = {k: v for k, v in attrs.items() if k != "uri"}
            conn.execute(
                "UPDATE nodes SET kind=?, uri=?, attrs_json=? WHERE id=?",
                (
                    new_kind,
                    new_uri,
                    json.dumps(attrs_for_db, sort_keys=True),
                    other_id,
                ),
            )

        nodes = [
            GraphNode(
                kind=new_kind,
                name=info["name"],
                path=package_path,
                line=None,
                attrs=attrs,
            )
        ]
        edges = []
        for file_path in contained:
            edges.append(
                GraphEdge(
                    src=(new_kind, info["name"], package_path),
                    dst=("file", file_path, file_path),
                    kind="contains",
                    attrs={},
                )
            )
        upsert.upsert_records(conn, as_graph_records(nodes=nodes, edges=edges))

        # collect deps from manifests and feed the shared
        # dep_acc / used_by_pairs / internal_pkg_edges accumulators.
        # Python: project.dependencies + dependency-groups (PEP 508 specifiers).
        # JavaScript: dep_specs dict from _read_package_json (raw version strings).
        consumer_name = info["name"]
        consumer_rel_path = package_path
        consumer_kind = new_kind
        consumer_norm = _normalize_name(consumer_name)
        if info["language"] == "python":
            all_dep_strs: list[str] = list(info["dependencies"])
            for group_entries in info.get("dep_groups", {}).values():
                all_dep_strs.extend(group_entries)
            for s in all_dep_strs:
                dep_name = _extract_dep_name(s)
                if dep_name is None:
                    continue
                dep_norm = _normalize_name(dep_name)
                # a dependency naming a workspace
                # package/app must NOT become a `dependency` node.
                # Cross-ecosystem: matched purely on the normalized nam.
                # Record it as an internal package→package relationship instead;
                # skip self-dependencies.
                if dep_norm in workspace_names and dep_norm != consumer_norm:
                    target_repo = _owning_repo(global_ws, dep_norm, current_repo)
                    if target_repo == current_repo or current_repo is None:
                        target_kind, target_name, target_rel_path = workspace_kinds[dep_norm]
                        internal_pkg_edges.append(
                            (
                                consumer_name,
                                consumer_rel_path,
                                consumer_kind,
                                target_name,
                                target_rel_path,
                                target_kind,
                            )
                        )
                    elif deferred_cross_repo is not None:
                        g_kind, g_name, g_rel, _ = global_ws[dep_norm]
                        deferred_cross_repo.append(
                            (consumer_kind, consumer_name, consumer_rel_path, g_kind, g_name, g_rel)
                        )
                    continue
                key = ("pypi", dep_name)
                bucket = dep_acc.setdefault(key, {"versions_in_use": []})
                if s not in bucket["versions_in_use"]:
                    bucket["versions_in_use"].append(s)
                # Python deps are never dev in this model (dep_groups treated as runtime).
                used_by_pairs.append((consumer_name, consumer_rel_path, consumer_kind, "pypi", dep_name, False))
        elif info["language"] == "javascript":
            # iterate dep_specs (name->spec, runtime wins on collision).
            # is_dev = name came ONLY from devDependencies (not in runtime set).
            runtime_set: set[str] = info.get("_runtime_dep_names", set())
            dev_set = set(info.get("dev_dependencies", []))
            for dep_name, raw_spec in info.get("dep_specs", {}).items():
                dep_norm = _normalize_name(dep_name)
                # Internal workspace package → depends_on_package; skip self-deps.
                if dep_norm in workspace_names and dep_norm != consumer_norm:
                    target_repo = _owning_repo(global_ws, dep_norm, current_repo)
                    if target_repo == current_repo or current_repo is None:
                        target_kind, target_name, target_rel_path = workspace_kinds[dep_norm]
                        internal_pkg_edges.append(
                            (
                                consumer_name,
                                consumer_rel_path,
                                consumer_kind,
                                target_name,
                                target_rel_path,
                                target_kind,
                            )
                        )
                    elif deferred_cross_repo is not None:
                        g_kind, g_name, g_rel, _ = global_ws[dep_norm]
                        deferred_cross_repo.append(
                            (consumer_kind, consumer_name, consumer_rel_path, g_kind, g_name, g_rel)
                        )
                    continue
                key = ("npm", dep_name)
                bucket = dep_acc.setdefault(key, {"versions_in_use": []})
                if raw_spec and raw_spec not in bucket["versions_in_use"]:
                    bucket["versions_in_use"].append(raw_spec)
                # is_dev: name is in devDependencies AND NOT in runtime dependencies.
                is_dev = dep_name in dev_set and dep_name not in runtime_set
                used_by_pairs.append((consumer_name, consumer_rel_path, consumer_kind, "npm", dep_name, is_dev))

    # Emit dependency nodes (one per (ecosystem, name)) + used_by edges.
    dep_nodes: list[GraphNode] = []
    dependency_paths: dict[tuple[str, str], str] = {}
    for (ecosystem, name), bucket in sorted(dep_acc.items()):
        versions = sorted(set(bucket["versions_in_use"]))
        dependency_path = f"dependency:{ecosystem}:{name}"
        dependency_paths[(ecosystem, name)] = dependency_path
        dep_nodes.append(
            GraphNode(
                kind="dependency",
                name=name,
                path=dependency_path,
                line=None,
                attrs={
                    "uri": dependency_uri(ecosystem, name),
                    "ecosystem": ecosystem,
                    "name": name,
                    "url": _dependency_registry_url(ecosystem, name),
                    "versions_in_use": versions,
                },
            )
        )
    # used_by edges: dedupe per (consumer_name, dep_name) so a dep listed
    # twice in one manifest (e.g. once in [project.dependencies], once in
    # [dependency-groups]) collapses to exactly one edge:
    # src uses consumer_kind so App consumers emit src=("app", ...).
    # is_dev=True → attrs={"dev": True}; False/omitted → attrs={}.
    dep_edges: list[GraphEdge] = []
    seen_edges: set[tuple[str, str, str]] = set()
    for consumer_name, consumer_rel_path, consumer_kind, ecosystem, dep_name, is_dev in used_by_pairs:
        if (consumer_name, ecosystem, dep_name) in seen_edges:
            continue
        seen_edges.add((consumer_name, ecosystem, dep_name))
        edge_attrs: dict[str, Any] = {"dev": True} if is_dev else {}
        dep_path = dependency_paths[(ecosystem, dep_name)]
        dep_edges.append(
            GraphEdge(
                src=(consumer_kind, consumer_name, consumer_rel_path),
                dst=("dependency", dep_name, dep_path),
                kind="used_by",
                attrs=edge_attrs,
            )
        )
    # Pfor each internal package→package dependency, emit TWO
    # same-direction (consumer → internal package) edges, INTENTIONALLY redundant:
    #   - `used_by` stays the universal "consumer uses X" relationship (uniform
    #     across external deps and internal packages), here retargeted to the real
    #     package/app node instead of a (now-suppressed) `dependency` node;
    #   - `depends_on_package` carries the package-level semantic that index
    #     nesting and `gw graph describe-package` consume.
    # Do NOT collapse these into one edge — both surfaces depend on it.
    # Same per-(consumer, target) dedupe as the external used_by edges above.
    for (
        consumer_name,
        consumer_rel_path,
        consumer_kind,
        target_name,
        target_rel_path,
        target_kind,
    ) in internal_pkg_edges:
        if (consumer_name, target_kind, target_name) in seen_edges:
            continue
        seen_edges.add((consumer_name, target_kind, target_name))
        src = (consumer_kind, consumer_name, consumer_rel_path)
        dst = (target_kind, target_name, target_rel_path)
        dep_edges.append(GraphEdge(src=src, dst=dst, kind="used_by", attrs={}))
        dep_edges.append(GraphEdge(src=src, dst=dst, kind=_DEPENDS_ON_PACKAGE_KIND, attrs={}))
    if dep_nodes or dep_edges:
        upsert.upsert_records(conn, as_graph_records(nodes=dep_nodes, edges=dep_edges))


def link_cross_repo_packages(conn: sqlite3.Connection, deferred: list) -> None:
    """Emit cross-repo used_by + depends_on_package edges after all members exist.

    `deferred` carries (consumer_kind, consumer_name, consumer_rel, target_kind,
    target_name, target_rel) tuples collected across members in `refresh`. The
    (kind, name, rel) endpoint tuples match the existing internal-edge node-key
    convention, so dst resolves to the sibling member's real package/app node
    rather than inserting a stub. Run once, after every member is stamped.
    """
    if not deferred:
        return
    edges: list[GraphEdge] = []
    seen: set[tuple[str, str, str]] = set()
    for consumer_kind, consumer_name, consumer_rel, target_kind, target_name, target_rel in deferred:
        if (consumer_name, target_kind, target_name) in seen:
            continue
        seen.add((consumer_name, target_kind, target_name))
        src = (consumer_kind, consumer_name, consumer_rel)
        dst = (target_kind, target_name, target_rel)
        edges.append(GraphEdge(src=src, dst=dst, kind="used_by", attrs={}))
        edges.append(GraphEdge(src=src, dst=dst, kind=_DEPENDS_ON_PACKAGE_KIND, attrs={}))
    upsert.upsert_records(conn, as_graph_records(nodes=[], edges=edges))
