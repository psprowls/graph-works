"""Find and discard structural scan writes in a previously clean bundle."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_core.workspace.provenance import run_git


@dataclass(frozen=True, slots=True)
class BundleChange:
    path: str
    untracked: bool


def bundle_changes(layout: WorkspaceLayout) -> tuple[BundleChange, ...] | None:
    """List bundle changes relative to the bundle, including both sides of renames."""
    bundle = layout.bundle_dir
    top = run_git(bundle, "rev-parse", "--show-toplevel")
    if top is None:
        return None
    out = run_git(bundle, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--", ".")
    if out is None:
        return None
    toplevel, root = Path(top.strip()).resolve(), bundle.resolve()
    found: dict[str, BundleChange] = {}
    fields = out.split("\0")
    index = 0
    while index < len(fields):
        entry = fields[index]
        index += 1
        if len(entry) < 4:
            continue
        status, paths = entry[:2], [entry[3:]]
        if status[0] in "RC" or status[1] in "RC":
            paths.append(fields[index])
            index += 1
        for path in paths:
            member = toplevel / path
            if member.is_relative_to(root):
                rel = member.relative_to(root).as_posix()
                found[rel] = BundleChange(rel, status == "??")
    return tuple(found[path] for path in sorted(found))


def discard_bundle_changes(layout: WorkspaceLayout, changes: Sequence[BundleChange]) -> None:
    """Restore tracked paths from HEAD and remove files created by the scan."""
    bundle = layout.bundle_dir
    tracked = [change.path for change in changes if not change.untracked]
    in_head = [path for path in tracked if run_git(bundle, "cat-file", "-e", f"HEAD:./{path}") is not None]
    if in_head and run_git(bundle, "restore", "--source=HEAD", "--staged", "--worktree", "--", *in_head) is None:
        raise OSError("cannot restore tracked bundle changes")
    for path in tracked:
        if path not in in_head:
            if run_git(bundle, "rm", "-q", "--cached", "--ignore-unmatch", "--", path) is None:
                raise OSError(f"cannot remove staged bundle path {path}")
            (bundle / path).unlink(missing_ok=True)
    for change in changes:
        if change.untracked:
            (bundle / change.path).unlink(missing_ok=True)
