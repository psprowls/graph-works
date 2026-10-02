"""`run_work_affecting`: the active work items in one repository whose `affects` meet a path."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from work_tracker_okf.affects import code_affects
from work_tracker_okf.items import WorkItem, item_index
from work_tracker_okf.vocabulary import TERMINAL_STATUSES

from graph_works_core.guidance.assembly import affects_overlap
from graph_works_core.work.commands import run_work_list
from graph_works_core.workspace.errors import WorkspaceError
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_core.workspace.repos import declared_repositories, resolve_item_repo


@dataclass(frozen=True, slots=True)
class AffectingRow:
    """One matching item and the `affects` entries that overlap the queried path."""

    item: WorkItem
    matching: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class WorkAffecting:
    """The rows for one `(repo, path)` query, sorted by item path; `refusal` names a repository nothing declares."""

    repo: str
    path: str
    rows: tuple[AffectingRow, ...]
    refusal: Literal["unknown-repository"] | None


def run_work_affecting(layout: WorkspaceLayout, repo: str, path: str) -> WorkAffecting:
    """Non-archived, non-terminal items resolving to *repo* whose `affects` equal, contain or sit under *path*.

    Repository resolution is `resolve_item_repo`'s; an item it cannot place (several repositories declared,
    none named) belongs to no repository and is skipped. Never writes.
    """
    if repo not in declared_repositories(layout):
        return WorkAffecting(repo, path, (), "unknown-repository")
    items = tuple(item for item in run_work_list(layout) if item.work_status not in TERMINAL_STATUSES)
    index = item_index(items)
    rows: list[AffectingRow] = []
    for item in items:
        try:
            resolved = resolve_item_repo(layout, item, index).name
        except WorkspaceError:
            continue
        if resolved != repo:
            continue
        matching = tuple(entry for entry in code_affects(item.affects) if affects_overlap([entry], [path]))
        if matching:
            rows.append(AffectingRow(item, matching))
    return WorkAffecting(repo, path, tuple(rows), None)
