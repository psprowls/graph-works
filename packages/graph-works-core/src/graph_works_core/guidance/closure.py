"""The `affects` closure: a work item's `affects` paths as scanner URIs with a specificity tier.

Tiers (D-001, amended): 0 `file:` > 1 the directory owners — `pkg:`/`app:`,
`agent_plugin:` and `test_suite:` — > 2 one-hop outgoing internal
dependencies and the external `dependency:` URIs of the tier-1 packages and
apps > 3 `repo:`. No transitive walk, no dependents. Every URI is
repo-qualified — graph file paths are repo-relative and collide across
repositories.

Tier 1 is directory ownership. A matched file admits, per owner group, the
owners at its longest directory prefix; the groups — packages and apps
together, agent plugins, test suites — contest that prefix separately, so a
test suite inside a package directory never steals the package's admission.
An `affects` path that exactly names an owner's directory admits it too, even
when the owner holds no file. Tier-2 neighbours come from packages and apps
only.

Nothing here raises for a missing graph, an unknown repository or an
unmatched path: each is a warning, and a missing graph means "no guidance".
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from code_graph_io import GraphReader, NodeRecord
from code_graph_io.uri import RepoContext, file_uri, repo_uri

from graph_works_core.guidance.claims import ClaimRow

Tier = Literal[0, 1, 2, 3]

#: Owner kinds whose one-hop dependencies are tier 2.
_TIER2_SEEDS = frozenset({"package", "app"})

WARN_NO_GRAPH = "no code graph: run `gw scan` to build one; the closure is empty"
WARN_NO_REPO = "the work item has no `repo:`; the closure is empty"
WARN_UNKNOWN_REPO = "repository {repo!r} is not in the code graph; the closure is empty"
WARN_UNMATCHED_PATH = "affects path {path!r} matches nothing in the code graph"


@dataclass(frozen=True)
class ClosureEntry:
    uri: str
    tier: Tier
    why: str


@dataclass(frozen=True)
class Closure:
    entries: tuple[ClosureEntry, ...]  # sorted by (tier, uri)
    warnings: tuple[str, ...]

    def uris(self) -> dict[str, ClosureEntry]:
        return {entry.uri: entry for entry in self.entries}


@dataclass(frozen=True)
class MatchedClaim:
    row: ClaimRow
    tier: Tier
    uri: str
    why: str


def _normalise(path: str) -> str:
    return path.removeprefix("./").rstrip("/")


def _under(path: str, directory: str) -> bool:
    """*path* is *directory* or lies beneath it — a directory prefix, not a string prefix."""
    return directory == "" or path == directory or path.startswith(directory + "/")


def _repo_context(reader: GraphReader, name: str) -> RepoContext | None:
    for node in reader.list_repositories():
        uri = node.attrs.get("uri")
        if node.name == name and isinstance(uri, str) and uri.startswith("repo:"):
            org, _, repo = uri.removeprefix("repo:").partition("/")
            if org and repo:
                return RepoContext(org, repo)
    return None


class _Admit:
    """Most-specific-tier-wins accumulator."""

    def __init__(self) -> None:
        self.entries: dict[str, ClosureEntry] = {}

    def __call__(self, uri: str, tier: Tier, why: str) -> None:
        current = self.entries.get(uri)
        if current is None or tier < current.tier:
            self.entries[uri] = ClosureEntry(uri, tier, why)


def affects_closure(reader: GraphReader | None, *, repo: str | None, affects: Sequence[str]) -> Closure:
    if reader is None:
        return Closure((), (WARN_NO_GRAPH,))
    if not repo:
        return Closure((), (WARN_NO_REPO,))
    ctx = _repo_context(reader, repo)
    if ctx is None:
        return Closure((), (WARN_UNKNOWN_REPO.format(repo=repo),))

    repo_node_uri = repo_uri(ctx)
    file_prefix = file_uri(ctx, "")
    files = [uri.removeprefix(file_prefix) for uri in reader.file_uris() if uri.startswith(file_prefix)]

    def in_repo(nodes: Sequence[NodeRecord]) -> list[NodeRecord]:
        return [
            node
            for node in nodes
            if node.attrs.get("repo") == repo_node_uri
            and isinstance(node.attrs.get("uri"), str)
            and node.path is not None
        ]

    # Owner groups contest the longest directory prefix separately. Packages
    # and apps seed tier 2 (`_TIER2_SEEDS`); plugins and suites own only.
    packages = in_repo([*reader.list_packages(), *reader.list_apps()])
    groups = (packages, in_repo(reader.list_agent_plugins()), in_repo(reader.list_test_suites()))

    admit = _Admit()
    warnings: list[str] = []
    seeds: set[str] = set()

    def owned(node: NodeRecord, why: str) -> None:
        uri = str(node.attrs["uri"])
        if node.kind in _TIER2_SEEDS:
            seeds.add(uri)
        admit(uri, 1, why)

    def owners_of(path: str) -> list[NodeRecord]:
        found: list[NodeRecord] = []
        for group in groups:
            candidates = [node for node in group if _under(path, _normalise(node.path or ""))]
            if candidates:
                longest = max(len(_normalise(node.path or "")) for node in candidates)
                found.extend(node for node in candidates if len(_normalise(node.path or "")) == longest)
        return found

    for raw in affects:
        path = _normalise(raw)
        if not path:
            # "./" and "/" both normalise to "" -- not a directory prefix of
            # anything (`_under`'s `directory == ""` clause would otherwise
            # make every file match, and a root package at path "" would
            # match exactly): treat as unmatched rather than "the whole repo".
            warnings.append(WARN_UNMATCHED_PATH.format(path=raw))
            continue
        matched_files = [rel for rel in files if _under(rel, path)]
        exact_owners = [node for group in groups for node in group if _normalise(node.path or "") == path]
        if not matched_files and not exact_owners:
            warnings.append(WARN_UNMATCHED_PATH.format(path=raw))
            continue
        for rel in matched_files:
            admit(file_prefix + rel, 0, f"file under {path}")
            for node in owners_of(rel):
                owned(node, f"contains a file under {path}")
        for node in exact_owners:
            owned(node, f"{node.kind} directory {path}")

    for owner_uri in sorted(seeds):
        for dependency_uri in reader.internal_dependency_uris_of(uri=owner_uri):
            admit(dependency_uri, 2, f"internal dependency of {owner_uri}")
        for dependency_uri in reader.external_dependencies_of(uri=owner_uri):
            admit(dependency_uri, 2, f"external dependency of {owner_uri}")

    admit(repo_node_uri, 3, f"repository {repo}")
    entries = tuple(sorted(admit.entries.values(), key=lambda entry: (entry.tier, entry.uri)))
    return Closure(entries, tuple(warnings))


def match_claims(
    rows: Sequence[ClaimRow], closure: Closure, *, include_superseded: bool = False
) -> tuple[MatchedClaim, ...]:
    """Rows whose `about` meets the closure, at their most specific tier, ordered `(tier, page, id)`."""
    index = closure.uris()
    matched: list[MatchedClaim] = []
    for row in rows:
        if row.superseded and not include_superseded:
            continue
        hits = [index[uri] for uri in row.about if uri in index]
        if not hits:
            continue
        best = min(hits, key=lambda entry: (entry.tier, entry.uri))
        matched.append(MatchedClaim(row, best.tier, best.uri, best.why))
    return tuple(sorted(matched, key=lambda m: (m.tier, m.row.page, m.row.id)))
