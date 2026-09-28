"""Pure selection and preparation of repository-local integration anchors."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePath

from subagents_io.dispatch import WorktreeAction
from work_tracker_okf.items import WorkItem

from graph_works_core.workspace.finish import enclosing_owner as enclosing_owner
from graph_works_core.workspace.repo_context import RepositoryContext
from graph_works_core.workspace.repos import ItemRepo
from graph_works_core.workspace.workspace_branch import WORKSPACE_REPO


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


def _verified_stamp(
    owner: WorkItem,
    *,
    repos: Mapping[str, ItemRepo],
    repo: ItemRepo,
    context: RepositoryContext,
    strict_scalar_fields: bool,
) -> Anchor | AnchorRefusal | None:
    """Select the exact repository stamp and prove its inventory/path provenance."""
    own_repo = repos.get(owner.path)
    if own_repo is None:
        return AnchorRefusal("worktree-unprovable", f"resolve repository for integration owner {owner.path}")
    own = own_repo.name == repo.name
    stamp = owner.repo_stamps.get(repo.name) if repo.name is not None and not own else None
    path, branch = (
        (owner.worktree, owner.branch) if own else ((stamp.worktree, stamp.branch) if stamp else (None, None))
    )
    if "repo_stamps" in owner.invalid_optional_fields or (
        own
        and (
            bool(path) != bool(branch)
            or (strict_scalar_fields and {"worktree", "branch"}.intersection(owner.invalid_optional_fields))
        )
    ):
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
        anchor = Anchor(path, branch)
    return anchor


def reader_anchor(
    owner: WorkItem,
    *,
    repos: Mapping[str, ItemRepo],
    repo: ItemRepo,
    context: RepositoryContext,
) -> Anchor | AnchorRefusal | None:
    """Verify reader provenance, rejecting invalid scalar fields but allowing dirtiness."""
    return _verified_stamp(owner, repos=repos, repo=repo, context=context, strict_scalar_fields=True)


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
    anchor = _verified_stamp(owner, repos=repos, repo=repo, context=context, strict_scalar_fields=False)
    if isinstance(anchor, AnchorRefusal):
        return anchor
    if anchor is not None and context.checkout_usable_by_path.get(anchor.worktree) is not True:
        return AnchorRefusal(
            "worktree-unprovable",
            f"repair integration anchor on {owner.path}: selected checkout is dirty or unreadable",
        )
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
        action = WorktreeAction("reuse", path, branch, None, True, None, None)
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
            None,
        )
    return AnchorPreparation(owner.path, owner.phase, repo, branch, base, action)


@dataclass(frozen=True, slots=True)
class WorkspacePlacement:
    worktree: str
    branch: str


@dataclass(frozen=True, slots=True)
class WorkspacePreparation:
    owner_path: str
    owner_phase: str | None
    worktree: str
    branch: str
    base_branch: str


def workspace_chain(item: WorkItem, items: Mapping[str, WorkItem]) -> tuple[WorkItem, ...]:
    """Return enclosing integration owners outermost first, then the item."""
    chain = [item]
    owner = enclosing_owner(item, items)
    while owner is not None:
        chain.append(owner)
        owner = enclosing_owner(owner, items)
    return tuple(reversed(chain))


def workspace_worktree_path(worktrees_dir: str, path: str, type_: str) -> str:
    """Return the workspace-owned checkout path for an item."""
    from .commands import _stable_stem

    return str(Path(worktrees_dir) / "workspace" / _stable_stem(path, type_))


def verify_workspace_stamp(owner: WorkItem, context: RepositoryContext) -> WorkspacePlacement | AnchorRefusal | None:
    """Prove the owner's workspace stamp against observed repository state."""
    if "repo_stamps" in owner.invalid_optional_fields:
        return AnchorRefusal("worktree-unprovable", f"repair malformed repo_stamps on {owner.path}")
    stamp = owner.repo_stamps.get(WORKSPACE_REPO)
    if stamp is None:
        return None
    matches = context.inventory.get(stamp.branch, ())
    if len(matches) > 1:
        return AnchorRefusal(
            "worktree-ambiguous", f"repair ambiguous workspace branch {stamp.branch!r} on {owner.path}"
        )
    if matches != (stamp.worktree,) or context.path_exists.get(stamp.worktree) is not True:
        return AnchorRefusal(
            "worktree-unprovable", f"repair workspace stamp on {owner.path}: path and branch are not verified"
        )
    if context.checkout_usable_by_path.get(stamp.worktree) is not True:
        return AnchorRefusal(
            "worktree-unprovable", f"workspace checkout {stamp.worktree!r} for {owner.path} is dirty or unreadable"
        )
    return WorkspacePlacement(stamp.worktree, stamp.branch)


def select_workspace(
    item: WorkItem, *, items: Mapping[str, WorkItem], context: RepositoryContext, worktrees_dir: str
) -> WorkspacePlacement | WorkspacePreparation | AnchorRefusal:
    """Verify the chain outermost first, requesting the first missing link."""
    from .commands import branch_name

    base = context.default_base
    placement: WorkspacePlacement | None = None
    for owner in workspace_chain(item, items):
        verified = verify_workspace_stamp(owner, context)
        if isinstance(verified, AnchorRefusal):
            return verified
        if verified is None:
            return WorkspacePreparation(
                owner.path,
                owner.phase,
                workspace_worktree_path(worktrees_dir, owner.path, owner.type),
                branch_name(owner.path, owner.type),
                base,
            )
        base, placement = verified.branch, verified
    assert placement is not None  # The chain always ends with item.
    return placement
