"""Shared workspace stamp verification and returned-coverage destination."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from work_tracker_okf.advance import RefusalReason
from work_tracker_okf.items import WorkItem

from graph_works_core.workspace.commits import commit_target
from graph_works_core.workspace.layout import WorkspaceLayout, layout_for
from graph_works_core.workspace.repo_context import RepositoryContext, observe_repository, repository_identity
from graph_works_core.workspace.workspace_branch import WORKSPACE_REPO, workspace_repo


@dataclass(frozen=True, slots=True)
class AnchorRefusal:
    kind: str
    reason: str


@dataclass(frozen=True, slots=True)
class WorkspacePlacement:
    worktree: str
    branch: str


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


@dataclass(frozen=True, slots=True)
class ReturnDestination:
    bundle_root: Path
    layout: WorkspaceLayout
    worktree: str | None
    branch: str | None
    same_root: bool
    identity: str | None = None


def confined_member(root: Path, member: str) -> Path:
    """Reject absolute/traversing paths and symlink escapes before any read."""
    rel = Path(member)
    target = root / rel
    if not member or rel.is_absolute() or ".." in rel.parts or not target.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"member outside bundle: {member!r}")
    return target


def resolve_destination(
    layout: WorkspaceLayout, items: Mapping[str, WorkItem], item: WorkItem
) -> ReturnDestination | tuple[RefusalReason, str]:
    """Resolve only this item's stamp; an invalid stamp never falls back."""
    repo, _note = workspace_repo(layout)
    if repo is None:
        return ReturnDestination(layout.bundle_dir, layout, None, None, True)
    context = observe_repository(layout.root)
    if not context.identity_known or not context.inventory_known:
        return "return-destination-unverified", "workspace repository identity or inventory is unprovable"
    stamp = verify_workspace_stamp(item, context)
    if isinstance(stamp, AnchorRefusal):
        return "return-destination-unverified", stamp.reason
    if stamp is None:
        return ReturnDestination(layout.bundle_dir, layout, None, None, True)
    try:
        content = layout_for(
            stamp.worktree,
            bundle_dir=layout.bundle_dir.relative_to(layout.root).as_posix(),
            config_dir=layout.config_dir.relative_to(layout.root).as_posix(),
        )
        if not content.bundle_dir.resolve().is_relative_to(content.root):
            raise ValueError("content bundle escapes checkout")
        if repository_identity(content.root) != context.identity:
            raise ValueError("content checkout repository identity changed")
        target, _ = commit_target(content)
        if target != content.root:
            raise ValueError("content checkout does not commit")
    except (OSError, ValueError) as exc:
        return "return-destination-unverified", str(exc)
    return ReturnDestination(
        content.bundle_dir, content, stamp.worktree, stamp.branch, content.root == layout.root, context.identity
    )
