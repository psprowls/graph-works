"""Layout-level entry points over the claims index and the `affects` closure.

These resolve what the pure modules take as arguments: the bundle (loaded
once, work references ignored), the graph (opened and closed here), and the
work item. An unknown item is a refusal, never a raise (ADR
2026-08-13-command-modules rule 5).

The item's repository is resolved with core's precedence
(`workspace.repos.resolve_item_repo`): the nearest `repo:` on the item or an
ancestor, else the one declared repository. Unlike that resolver, nothing
here refuses — an item with no `repo:` among several declared repositories is
a warning and an empty closure. Only a malformed `workspace.yaml`
(`WorkspaceConfigError`) propagates.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from code_graph_io import GraphNotInitializedError, SchemaMismatchError, open_reader
from okf_io import load_bundle
from work_tracker_okf.hierarchy import declared_repo
from work_tracker_okf.items import IGNORE, WorkItem, item_index, load_items, unreadable_detail

from graph_works_core.graph.commands import graph_target
from graph_works_core.guidance.claims import (
    ClaimRow,
    ClaimsRefresh,
    build_claims,
    claims_about,
    read_claims,
    refresh_claims,
)
from graph_works_core.guidance.closure import Closure, MatchedClaim, affects_closure, match_claims
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_core.workspace.repos import declared_repositories

WARN_AMBIGUOUS_REPO = (
    "the work item and its ancestors set no `repo:` and the workspace declares {count} repositories "
    "({names}); set `repo:` to choose one"
)


@dataclass(frozen=True)
class ClaimsShow:
    uri: str
    rows: tuple[ClaimRow, ...]


@dataclass(frozen=True)
class ClaimsClosureRun:
    path: str
    repo: str | None
    affects: tuple[str, ...]
    closure: Closure
    matched: tuple[MatchedClaim, ...]
    total_tokens: int
    refusal: Literal["unknown-item", "unreadable"] | None
    detail: str | None


def run_claims_refresh(layout: WorkspaceLayout, *, force: bool = False) -> ClaimsRefresh:
    bundle = load_bundle(layout.bundle_dir, ignore=IGNORE)
    return (build_claims if force else refresh_claims)(bundle, layout.cache_dir)


def run_claims_show(layout: WorkspaceLayout, uri: str, *, include_superseded: bool = False) -> ClaimsShow:
    rows = read_claims(load_bundle(layout.bundle_dir, ignore=IGNORE), layout.cache_dir)
    return ClaimsShow(uri, claims_about(rows, uri, include_superseded=include_superseded))


def open_closure(layout: WorkspaceLayout, *, repo: str | None, affects: Sequence[str]) -> Closure:
    """The closure over this workspace's graph. Uninitialized, schema-mismatched and zero-node all mean "no graph"."""
    try:
        reader = open_reader(graph_dir=graph_target(layout).graph_dir)
    except (GraphNotInitializedError, SchemaMismatchError):
        return affects_closure(None, repo=repo, affects=affects)
    try:
        if reader.node_count() == 0:
            return affects_closure(None, repo=repo, affects=affects)
        return affects_closure(reader, repo=repo, affects=affects)
    finally:
        reader.close()


def _item_repo(layout: WorkspaceLayout, item: WorkItem, items: Mapping[str, WorkItem]) -> tuple[str | None, str | None]:
    """`(repository name, warning)`: the nearest `repo:` over the item's chain, else the sole declared one.

    Several declared and none chosen is `(None, warning)`, never a raise. A
    malformed `workspace.yaml` raises `WorkspaceConfigError`; an absent one
    declares nothing.
    """
    name, _ = declared_repo(item, items)
    if name is not None:
        return name, None
    repositories = declared_repositories(layout)
    if len(repositories) == 1:
        return next(iter(repositories)), None
    if len(repositories) > 1:
        return None, WARN_AMBIGUOUS_REPO.format(count=len(repositories), names=", ".join(sorted(repositories)))
    return None, None


def run_claims_closure(layout: WorkspaceLayout, path: str, *, include_superseded: bool = False) -> ClaimsClosureRun:
    bundle = load_bundle(layout.bundle_dir, ignore=IGNORE)
    items = item_index(load_items(bundle))
    item = items.get(path)
    if item is None:
        detail = unreadable_detail(bundle, path)
        refusal: Literal["unknown-item", "unreadable"] = "unreadable" if detail is not None else "unknown-item"
        return ClaimsClosureRun(path, None, (), Closure((), ()), (), 0, refusal, detail)
    repo, warning = _item_repo(layout, item, items)
    closure = open_closure(layout, repo=repo, affects=item.affects)
    if warning is not None:
        closure = Closure(closure.entries, (warning, *closure.warnings))
    matched = match_claims(read_claims(bundle, layout.cache_dir), closure, include_superseded=include_superseded)
    return ClaimsClosureRun(path, repo, item.affects, closure, matched, sum(m.row.tokens for m in matched), None, None)
