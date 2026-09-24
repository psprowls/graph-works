"""Reconcile repository-scoped Dependency facets from the workspace manifest inventory."""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from code_graph_io import upsert
from code_graph_io.packages import ManifestDependency, ManifestPackage
from code_graph_io.records import GraphNode, as_graph_records
from code_graph_io.uri import RepoContext, dependency_path, dependency_uri, pkg_uri, repo_uri

_OWNERSHIP_KEY = "manifest_dependency_reconciler_v2"
_EdgeIdentity = tuple[str, str, str]


_ScopedKey = tuple[RepoContext, str, str]  # (declaring repository, ecosystem, normalized name)


@dataclass(frozen=True, slots=True)
class WorkspaceImplementation:
    package_uri: str
    package_key: tuple[str, str, str]
    repo_uri: str


def _version_entry(key: _ScopedKey, dependency: ManifestDependency) -> str:
    return f"{key[2]}{dependency.spec}" if dependency.ecosystem == "pypi" else dependency.spec


def _normalize_name(ecosystem: str, name: str) -> str:
    """Return the stable persisted identity for an ecosystem package name.

    PyPI uses the PEP 503 distribution name: runs of ``-``, ``_`` and ``.``
    collapse to a single ``-``, lowercased. This is deliberately NOT the
    *import* name (``code_graph_io``) that ``import_scan`` derives -- a
    Dependency is an installation contract, so its identity is the name the
    index publishes, not the name Python imports.
    """
    if ecosystem == "pypi":
        return re.sub(r"[-_.]+", "-", name).lower()
    if ecosystem == "npm":
        return name.lower()
    raise ValueError(f"unsupported dependency ecosystem: {ecosystem!r}")


def _registry_url(ecosystem: str, name: str) -> str:
    if ecosystem == "pypi":
        return f"https://pypi.org/project/{name}/"
    if ecosystem == "npm":
        return f"https://www.npmjs.com/package/{name}"
    raise ValueError(f"unsupported dependency ecosystem: {ecosystem!r}")


def _identity(ecosystem: str, name: str) -> tuple[str, str]:
    return ecosystem, _normalize_name(ecosystem, name)


def _insert_uri_edge(conn: sqlite3.Connection, *, src_uri: str, kind: str, dst_uri: str) -> None:
    src = conn.execute("SELECT id FROM nodes WHERE uri=?", (src_uri,)).fetchone()
    dst = conn.execute("SELECT id FROM nodes WHERE uri=?", (dst_uri,)).fetchone()
    if src is None or dst is None:
        raise RuntimeError(f"cannot create {kind}: missing endpoint {src_uri} -> {dst_uri}")
    conn.execute(
        "INSERT INTO edges(src, dst, kind, attrs_json) VALUES (?, ?, ?, NULL) "
        "ON CONFLICT(src, dst, kind) DO UPDATE SET attrs_json=NULL",
        (src[0], dst[0], kind),
    )


def _load_ownership(conn: sqlite3.Connection) -> tuple[set[str], set[_EdgeIdentity]]:
    row = conn.execute("SELECT value FROM metadata WHERE key=?", (_OWNERSHIP_KEY,)).fetchone()
    if row is None:
        return set(), set()
    try:
        value = json.loads(row[0])
        dependency_uris = {item for item in value["dependency_uris"] if isinstance(item, str)}
        edge_identities = {
            (item[0], item[1], item[2])
            for item in value["edge_identities"]
            if isinstance(item, list) and len(item) == 3 and all(isinstance(part, str) for part in item)
        }
    except (KeyError, TypeError, json.JSONDecodeError):
        return set(), set()
    return dependency_uris, edge_identities


def _record_ownership(conn: sqlite3.Connection, dependency_uris: set[str], edge_identities: set[_EdgeIdentity]) -> None:
    value = json.dumps(
        {
            "dependency_uris": sorted(dependency_uris),
            "edge_identities": [list(edge) for edge in sorted(edge_identities)],
        },
        sort_keys=True,
    )
    conn.execute(
        "INSERT INTO metadata(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (_OWNERSHIP_KEY, value),
    )


def _delete_owned_edges(conn: sqlite3.Connection, edge_identities: set[_EdgeIdentity]) -> None:
    """Delete only relationships recorded by an earlier reconciliation pass."""
    for src_uri, kind, dst_uri in edge_identities:
        conn.execute(
            "DELETE FROM edges WHERE kind=? AND src=(SELECT id FROM nodes WHERE uri=?) "
            "AND dst=(SELECT id FROM nodes WHERE uri=?)",
            (kind, src_uri, dst_uri),
        )


def _delete_orphan_owned_dependency_nodes(conn: sqlite3.Connection, dependency_uris: set[str]) -> None:
    for uri in dependency_uris:
        conn.execute(
            "DELETE FROM nodes WHERE kind='dependency' AND uri=? AND "
            "NOT EXISTS (SELECT 1 FROM edges WHERE edges.src=nodes.id OR edges.dst=nodes.id)",
            (uri,),
        )


def reconcile_dependencies(
    conn: sqlite3.Connection,
    *,
    manifests: Sequence[ManifestPackage],
    virtual_repository_dependencies: Mapping[RepoContext, Sequence[ManifestDependency]],
) -> None:
    """Persist one Dependency node per (repository, declared dependency) and its edges.

    A node exists in repository R exactly when something in R declares the
    dependency -- a package manifest (a `pkg:` consumer) or a virtual
    repository root (a `repo:` consumer). Its `versions_in_use` is R's own
    declared specs, and only R's consumers point `used_by` at it. Each scoped
    node whose identity matches a distributable workspace manifest points
    `implemented_by` at that package, which may live in another repository.
    A distributable package nothing declares has no Dependency node.

    Runs outside any member's `set_current_repo`, so the repo stamp is written
    explicitly for each row rather than inherited from the current-repo scope.
    """
    implementations: dict[tuple[str, str], list[WorkspaceImplementation]] = {}
    versions: dict[_ScopedKey, set[str]] = {}
    declarations: list[tuple[_ScopedKey, str, bool]] = []
    previous_dependency_uris, previous_edge_identities = _load_ownership(conn)

    def declare(ctx: RepoContext, consumer_uri: str, dependency: ManifestDependency, is_package_consumer: bool) -> None:
        key: _ScopedKey = (ctx, *_identity(dependency.ecosystem, dependency.name))
        declarations.append((key, consumer_uri, is_package_consumer))
        bucket = versions.setdefault(key, set())
        if dependency.spec:
            bucket.add(_version_entry(key, dependency))

    for manifest in manifests:
        if not manifest.distributable:
            continue
        implementations.setdefault(_identity(manifest.ecosystem, manifest.name), []).append(
            WorkspaceImplementation(
                package_uri=pkg_uri(manifest.repo, manifest.name),
                package_key=("package", manifest.name, manifest.relative_path),
                repo_uri=repo_uri(manifest.repo),
            )
        )
        for dependency in manifest.dependencies:
            declare(manifest.repo, pkg_uri(manifest.repo, manifest.name), dependency, True)

    for repository, repository_dependencies in virtual_repository_dependencies.items():
        for dependency in repository_dependencies:
            declare(repository, repo_uri(repository), dependency, False)

    for values in implementations.values():
        values.sort(key=lambda implementation: implementation.package_uri)

    dependency_uris = {key: dependency_uri(key[0], key[1], key[2]) for key in versions}
    _delete_owned_edges(conn, previous_edge_identities)
    ordered = sorted(dependency_uris.items(), key=lambda item: item[1])
    dependency_nodes = [
        GraphNode(
            kind="dependency",
            name=key[2],
            path=dependency_path(key[0], key[1], key[2]),
            line=None,
            attrs={
                "uri": uri,
                "ecosystem": key[1],
                "name": key[2],
                "url": _registry_url(key[1], key[2]),
                "versions_in_use": sorted(versions[key]),
            },
        )
        for key, uri in ordered
    ]
    upsert.upsert_records(conn, as_graph_records(nodes=dependency_nodes))
    for key, uri in ordered:
        conn.execute("UPDATE nodes SET repo=? WHERE kind='dependency' AND uri=?", (repo_uri(key[0]), uri))

    edge_identities: set[_EdgeIdentity] = set()

    for key, uri in ordered:
        for implementation in implementations.get((key[1], key[2]), ()):
            edge_identities.add((uri, "implemented_by", implementation.package_uri))
            _insert_uri_edge(conn, src_uri=uri, kind="implemented_by", dst_uri=implementation.package_uri)

    for key, consumer_uri, is_package_consumer in declarations:
        dependency_uri_value = dependency_uris[key]
        edge_identities.add((consumer_uri, "used_by", dependency_uri_value))
        _insert_uri_edge(conn, src_uri=consumer_uri, kind="used_by", dst_uri=dependency_uri_value)
        if is_package_consumer:
            for implementation in implementations.get((key[1], key[2]), ()):
                edge_identities.add((consumer_uri, "depends_on_package", implementation.package_uri))
                _insert_uri_edge(
                    conn,
                    src_uri=consumer_uri,
                    kind="depends_on_package",
                    dst_uri=implementation.package_uri,
                )

    current_dependency_uris = set(dependency_uris.values())
    stale_dependency_uris = previous_dependency_uris - current_dependency_uris
    _delete_orphan_owned_dependency_nodes(conn, stale_dependency_uris)
    retained_dependency_uris = {
        uri
        for uri in stale_dependency_uris
        if conn.execute("SELECT 1 FROM nodes WHERE kind='dependency' AND uri=?", (uri,)).fetchone() is not None
    }
    _record_ownership(conn, current_dependency_uris | retained_dependency_uris, edge_identities)
