"""Pure selection and preparation of repository-local integration anchors."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import PurePath

from subagents_io.dispatch import WorktreeAction
from work_tracker_okf.items import WorkItem

from graph_works_core.workspace.repo_context import RepositoryContext
from graph_works_core.workspace.repos import ItemRepo


@dataclass(frozen=True, slots=True)
class AnchorPreparation:
    owner_path: str
    owner_phase: str | None
    repo: ItemRepo
    branch: str
    base_branch: str
    worktree: WorktreeAction


@dataclass(frozen=True, slots=True)
class Anchor:
    worktree: str
    branch: str


@dataclass(frozen=True, slots=True)
class AnchorRefusal:
    kind: str
    reason: str


def integration_branch(owner_path: str, owner_type: str) -> str:
    """Use the same deterministic branch independently in each repository."""
    from .commands import branch_name

    return branch_name(owner_path, owner_type)


def enclosing_owner(item: WorkItem, items: Mapping[str, WorkItem]) -> WorkItem | None:
    parent = item.parent_path
    seen: set[str] = set()
    while parent and parent not in seen:
        seen.add(parent)
        owner = items.get(parent)
        if owner is None:
            return None
        if owner.type in {"Epic", "Release"} or (
            owner.type == "Feature" and (owner.active_child_paths or owner.archived_child_paths)
        ):
            return owner
        parent = owner.parent_path
    return None


def select_anchor(
    owner: WorkItem,
    *,
    items: Mapping[str, WorkItem],
    repos: Mapping[str, ItemRepo],
    repo: ItemRepo,
    context: RepositoryContext,
    prepare: bool,
) -> Anchor | AnchorPreparation | AnchorRefusal | None:
    """Validate exact owner stamps; plan the outermost missing anchor first.

    A preparation (including adoption) must be recorded before it can become
    a child fork target. Descendant feature stamps never supply an anchor.
    """
    own_repo = repos.get(owner.path)
    if own_repo is None:
        return AnchorRefusal("worktree-unprovable", f"resolve repository for integration owner {owner.path}")
    own = own_repo.name == repo.name
    stamp = owner.repo_stamps.get(repo.name) if repo.name is not None and not own else None
    path, branch = (
        (owner.worktree, owner.branch) if own else ((stamp.worktree, stamp.branch) if stamp else (None, None))
    )
    if "repo_stamps" in owner.invalid_optional_fields or (own and bool(path) != bool(branch)):
        return AnchorRefusal("worktree-unprovable", f"repair invalid integration stamp on {owner.path}")
    anchor = None
    if path and branch:
        matches = context.inventory.get(branch, ())
        if len(matches) > 1:
            return AnchorRefusal("worktree-ambiguous", f"repair ambiguous integration stamp on {owner.path}")
        if matches != (path,) or context.path_exists.get(path) is not True:
            return AnchorRefusal(
                "worktree-unprovable",
                f"repair integration stamp on {owner.path}: path and branch are not verified in this repository",
            )
        if context.checkout_usable_by_path.get(path) is not True:
            return AnchorRefusal(
                "worktree-unprovable",
                f"repair integration anchor on {owner.path}: selected checkout is dirty or unreadable",
            )
        anchor = Anchor(path, branch)
    if not prepare:
        return anchor
    outer = enclosing_owner(owner, items)
    parent = None
    if outer is not None:
        selected = select_anchor(outer, items=items, repos=repos, repo=repo, context=context, prepare=True)
        if not isinstance(selected, Anchor):
            return selected
        parent = selected
    if anchor is not None:
        return anchor
    branch = integration_branch(owner.path, owner.type)
    base = parent.branch if parent else context.default_base
    matches = context.inventory.get(branch, ())
    if len(matches) > 1:
        return AnchorRefusal("worktree-ambiguous", f"repair ambiguous integration branch {branch!r}")
    if matches:
        path = matches[0]
        if context.path_exists.get(path) is not True:
            return AnchorRefusal("worktree-unprovable", f"repair missing integration worktree for {branch!r}")
        if context.checkout_usable_by_path.get(path) is not True:
            return AnchorRefusal("worktree-unprovable", f"repair dirty or unreadable integration checkout {path!r}")
        action = WorktreeAction("reuse", path, branch, None, True, None)
    else:
        if not context.branches_known or branch in context.branches:
            return AnchorRefusal(
                "worktree-unprovable",
                f"repair integration branch {branch!r}: branch availability or checkout cannot be proved",
            )
        # Do not adopt a merely similar directory under a different branch.
        if any(PurePath(p).name == owner.basename for paths in context.inventory.values() for p in paths):
            return AnchorRefusal("worktree-ambiguous", f"repair conflicting integration worktree for {owner.path}")
        action = WorktreeAction(
            "fork-child" if parent else "create-top-level",
            None,
            branch,
            base,
            None,
            parent.worktree if parent else None,
        )
    return AnchorPreparation(owner.path, owner.phase, repo, branch, base, action)
