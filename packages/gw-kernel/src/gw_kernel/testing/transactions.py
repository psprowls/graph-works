"""Shared test-only transaction support for installed package consumers.

Requires pytest from the test environment; runtime modules never import this support.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from datetime import date
from pathlib import Path

from okf_ext.moves import Move
from work_tracker_okf.init import install_bundle
from work_tracker_okf.mutation import DirectoryPrecondition, PlannedWrite, WorkMutationPlan

from gw_kernel.workspace.anchors import BUNDLE_LOCK_NAME
from gw_kernel.workspace.layout import WorkspaceLayout, layout_for


def workspace(tmp_path: Path) -> WorkspaceLayout:
    layout = layout_for(tmp_path / "workspace")
    layout.root.mkdir(parents=True, exist_ok=True)
    layout.manifest_path.write_text(
        "version: 1\nworkflow: {dispatch_rules: dispatch.yaml}\n", encoding="utf-8", newline="\n"
    )
    (layout.root / "dispatch.yaml").write_text("{}\n", encoding="utf-8", newline="\n")
    layout.bundle_dir.mkdir(parents=True)
    layout.cache_dir.mkdir(parents=True)
    installed = install_bundle(
        layout.bundle_dir, declarations_dir=layout.config_dir, today=date(2026, 8, 22), dry_run=False
    )
    assert installed.ok
    return layout


def snapshot(root: Path) -> dict[str, tuple[str, int, bytes | str | None]]:
    """A recursive content snapshot, tier-blind by construction.

    The windows-revalidated tier's bundle-root lock lives at
    `anchors.BUNDLE_LOCK_NAME`, root-scoped -- a file a POSIX bundle never
    carries (see the constant's docstring). Excluding it here mirrors
    `load_bundle()`'s own ADR-0028 root-scoped dot exclusion, so a
    before/after snapshot comparison reflects bundle *content*, not which
    durability tier happened to acquire the lock first.
    """
    snapshot: dict[str, tuple[str, int, bytes | str | None]] = {}
    pending = [root]
    while pending:
        current = pending.pop()
        for path in sorted(current.iterdir(), key=lambda entry: os.fsencode(entry.name), reverse=True):
            if current == root and path.name == BUNDLE_LOCK_NAME:
                continue
            relative = path.relative_to(root).as_posix()
            mode = stat.S_IMODE(path.lstat().st_mode)
            if path.is_symlink():
                snapshot[relative] = ("symlink", mode, str(path.readlink()))
            elif path.is_dir():
                snapshot[relative] = ("directory", mode, None)
                pending.append(path)
            else:
                snapshot[relative] = ("file", mode, path.read_bytes())
    return snapshot


def plan(
    layout: WorkspaceLayout,
    *,
    moves: tuple[Move, ...] = (),
    writes: tuple[PlannedWrite, ...] = (),
    deletes: tuple[str, ...] = (),
    mkdirs: tuple[str, ...] = (),
    validate_paths: tuple[str, ...] = (),
    directory_preconditions: tuple[DirectoryPrecondition, ...] = (),
    path_mapping: dict[str, str] | None = None,
) -> WorkMutationPlan:
    return WorkMutationPlan(
        root=layout.bundle_dir,
        operation="reparent",
        path_mapping={} if path_mapping is None else path_mapping,
        move_plan=None,
        moves=moves,
        writes=writes,
        deletes=deletes,
        mkdirs=mkdirs,
        warnings=("opaque reference retained",),
        refusals=(),
        validate_paths=validate_paths,
        directory_preconditions=directory_preconditions,
    )


def representable_mode(mode: int, *, directory: bool) -> int:
    """What *mode* reads back as after a chmod on this host.

    POSIX keeps every bit.  NTFS keeps only S_IWRITE, reporting 0o777/0o555 for
    a directory and 0o666/0o444 for a file (measured, not inferred -- see this
    item's design).  Tests assert preservation against this rather than against
    the mode they requested, so the assertion stays meaningful on both hosts
    instead of being skipped on one -- D-025's "whatever the platform can
    represent" contract, applied to the engine's own tests.
    """
    if sys.platform != "win32":
        return mode
    writable = mode & stat.S_IWRITE
    if directory:
        return 0o777 if writable else 0o555
    return 0o666 if writable else 0o444


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout


def init_git(root: Path, *, commit: bool = True) -> Path:
    """A real, deterministic git repo at *root*: local identity, no signing, no user hooks."""
    hooks = root / ".no-hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.name", "gw-test")
    git(root, "config", "user.email", "gw-test@example.invalid")
    git(root, "config", "commit.gpgsign", "false")
    git(root, "config", "core.hooksPath", str(hooks))
    if commit:
        git(root, "add", "-A")
        git(root, "commit", "-q", "--allow-empty", "-m", "workspace: seed")
    return root


def assert_workspace_commit(root: Path, subject: str) -> None:
    """One exact subject, no body, and no uncommitted work-lane changes."""
    assert git(root, "log", "-1", "--format=%s").strip() == subject
    assert git(root, "log", "-1", "--format=%b").strip() == ""
    assert git(root, "status", "--porcelain", "--", "okf/work") == ""
