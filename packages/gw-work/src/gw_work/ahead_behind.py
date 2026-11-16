"""`run_work_ahead_behind`: each placed item's commits ahead of and behind its merge target.

Never writes. Runs git through `gw_kernel.workspace.provenance` only; never cached.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from gw_kernel.workspace import provenance
from gw_kernel.workspace.errors import WorkspaceError
from gw_kernel.workspace.layout import WorkspaceLayout
from gw_kernel.workspace.repos import declared_repositories, resolve_item_repo
from gw_pipeline.placement import placement_for
from work_tracker_okf.items import WorkItem, item_index

AheadBehindStatus = Literal["ok", "branch-missing", "target-missing", "git-unavailable"]


@dataclass(frozen=True, slots=True)
class AheadBehindRow:
    """One placed item's branch, merge target and live counts; counts are `None` unless `status` is `ok`."""

    path: str
    repo: str
    branch: str
    merge_target: str
    ahead: int | None
    behind: int | None
    status: AheadBehindStatus


def _status(root: Path, branch: str, target: str) -> tuple[AheadBehindStatus, int | None, int | None]:
    present = provenance.local_branch_exists(root, branch)
    if present is None:
        return "git-unavailable", None, None
    if not present:
        return "branch-missing", None, None
    target_present = provenance.local_branch_exists(root, target)
    if target_present is None:
        return "git-unavailable", None, None
    if not target_present:
        return "target-missing", None, None
    counts = provenance.ahead_behind(root, target, branch)
    if counts is None:
        return "git-unavailable", None, None
    return "ok", counts[0], counts[1]


def run_work_ahead_behind(
    layout: WorkspaceLayout, items: Sequence[WorkItem], *, path: str | None = None
) -> tuple[AheadBehindRow, ...]:
    """One row per item in *items* that resolves to a declared repository, sorted by path.

    The merge target is `placement_for(...).parent_branch`; `default_base` is resolved once per repository per
    call. An item no repository resolves for is omitted. *path* narrows to that one item.
    """
    try:
        repositories = declared_repositories(layout)
    except WorkspaceError:
        return ()
    index = item_index(items)
    bases: dict[str, str] = {}
    rows: list[AheadBehindRow] = []
    for item in sorted(items, key=lambda candidate: candidate.path):
        if path is not None and item.path != path:
            continue
        try:
            repo = resolve_item_repo(layout, item, index, repositories=repositories)
        except WorkspaceError:
            continue
        if repo.name is None or repo.path is None:
            continue
        if repo.name not in bases:
            bases[repo.name] = provenance.default_base(repo.path)
        placement = placement_for(
            item,
            index,
            repo_name=repo.name,
            repo_root=repo.path,
            working_checkout=None,
            worktrees_dir=layout.worktrees_dir,
            default_base=bases[repo.name],
        )
        if placement is None:
            continue
        status, ahead, behind = _status(repo.path, placement.branch, placement.parent_branch)
        rows.append(
            AheadBehindRow(item.path, repo.name, placement.branch, placement.parent_branch, ahead, behind, status)
        )
    return tuple(rows)


__all__ = ["AheadBehindRow", "AheadBehindStatus", "run_work_ahead_behind"]
